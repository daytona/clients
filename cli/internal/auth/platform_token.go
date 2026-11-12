// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"
	"net/http"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/google/uuid"
)

// RFC 8693 constants.
const (
	grantTypeTokenExchange = "urn:ietf:params:oauth:grant-type:token-exchange"
	subjectTokenTypeJWT    = "urn:ietf:params:oauth:token-type:jwt"
)

// platformTokenSkew is how early a cached platform token is treated as
// expired, leaving a minute of slack for requests to finish.
const platformTokenSkew = 60 * time.Second

// InstallPlatformToken wires PlatformToken into the App.
func InstallPlatformToken(a *app.App) {
	a.PlatformTokenFn = func(ctx context.Context, projectID, rejected string) (string, bool, error) {
		return PlatformToken(ctx, a, projectID, rejected)
	}
}

// PlatformToken returns a valid project-scoped platform access token for the
// OIDC profile, or ok=false when this deployment does not issue them (the
// caller then mints a project key).
//
// Three phases like AccessToken: (1) under the lock, a cached unexpired
// token is returned; (2) outside the lock, the issuer probe and the RFC 8693
// exchange (bounded network); (3) under the lock again, a token a concurrent
// invocation exchanged meanwhile wins, else ours is stored. `rejected` is a
// cached token the server refused — dropped first so it is never handed back.
func PlatformToken(ctx context.Context, a *app.App, projectID, rejected string) (string, bool, error) {
	if a.CredentialSource != "profile-oidc" {
		return "", false, nil
	}
	if rejected != "" {
		if err := dropPlatformToken(a, rejected); err != nil {
			return "", false, err
		}
	}
	// Canonicalise FIRST: the cache is keyed by the project's UUID, and a
	// name — including the literal `default`, which project settings routes accept
	// as an alias but the token exchange does not — must hit the same entry
	// on every invocation instead of exchanging afresh each time.
	if parsed, err := uuid.Parse(projectID); err == nil {
		projectID = parsed.String()
	} else if a.ProjectResolver != nil && a.Profile.OrgID != "" {
		resolved, resolveErr := a.ProjectResolver(ctx, a.Profile.OrgID, projectID)
		if resolveErr != nil {
			return "", false, resolveErr
		}
		projectID = resolved
	}
	if tok, ok := cachedPlatformToken(a, projectID); ok {
		return tok, true, nil
	}

	anon := a.AnonClient()
	issuer, err := isPlatformIssuer(ctx, anon)
	if err != nil {
		return "", false, err
	}
	if !issuer {
		return "", false, nil
	}
	jwt, err := AccessToken(ctx, a)
	if err != nil {
		return "", false, err
	}
	mc, err := a.Client(ctx)
	if err != nil {
		return "", false, err
	}
	project, err := uuid.Parse(projectID)
	if err != nil {
		return "", false, cerr.Usage("project id %q is not a UUID", projectID)
	}
	var resp gen.TokenExchange
	err = mc.Do(ctx, http.MethodPost, "/auth/token", nil, gen.ExchangeTokenRequest{
		GrantType:        grantTypeTokenExchange,
		SubjectToken:     jwt,
		SubjectTokenType: subjectTokenTypeJWT,
		// The CLI requests an access token for the selected project.
		ProjectId: &project,
	}, &resp)
	if err != nil {
		if cerr.ExitCode(err) == cerr.ExitNotFound {
			return "", false, cerr.New(cerr.ExitNotFound, "project %s is not one you are a member of", projectID).
				WithHint("`daytona project list` shows the projects you can use; `daytona project use <id|name>` selects one")
		}
		return "", false, err
	}
	origin := a.APIURL()
	minted := config.PlatformToken{
		ProjectID: projectID,
		Token:     resp.AccessToken,
		ExpiresAt: time.Now().Add(time.Duration(resp.ExpiresIn) * time.Second),
		APIURL:    origin,
	}

	out := minted.Token
	err = a.Store.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles[a.Profile.Name]
		now := time.Now()
		kept := make([]config.PlatformToken, 0, len(pc.PlatformTokens)+1)
		for _, t := range pc.PlatformTokens {
			if t.ExpiresAt.Before(now.Add(platformTokenSkew)) {
				continue
			}
			if t.ProjectID == projectID && t.APIURL == origin {
				// A concurrent invocation exchanged first; its token is as
				// good as ours and already on disk — converge on it.
				out = t.Token
				continue
			}
			kept = append(kept, t)
		}
		if out == minted.Token {
			kept = append(kept, minted)
		} else {
			for _, t := range pc.PlatformTokens {
				if t.ProjectID == projectID && t.APIURL == origin && t.Token == out {
					kept = append(kept, t)
					break
				}
			}
		}
		pc.PlatformTokens = kept
		cr.Profiles[a.Profile.Name] = pc
		return false, true, nil
	})
	if err != nil {
		// The token still serves this invocation.
		a.Info("warning: could not cache the platform token (%v); the next invocation will exchange again", err)
	}
	return out, true, nil
}

// cachedPlatformToken returns an unexpired cached token for the project that
// this invocation's API minted, pruning expired ones under the lock.
func cachedPlatformToken(a *app.App, projectID string) (string, bool) {
	var tok string
	origin := a.APIURL()
	_ = a.Store.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles[a.Profile.Name]
		now := time.Now()
		kept := pc.PlatformTokens[:0]
		for _, t := range pc.PlatformTokens {
			if t.ExpiresAt.After(now.Add(platformTokenSkew)) {
				kept = append(kept, t)
				if t.ProjectID == projectID && t.APIURL == origin {
					tok = t.Token
				}
			}
		}
		pruned := len(kept) != len(pc.PlatformTokens)
		pc.PlatformTokens = kept
		if pruned {
			cr.Profiles[a.Profile.Name] = pc
		}
		return false, pruned, nil
	})
	return tok, tok != ""
}

// dropPlatformToken forgets one cached token the server refused.
func dropPlatformToken(a *app.App, token string) error {
	return a.Store.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles[a.Profile.Name]
		kept := pc.PlatformTokens[:0]
		for _, t := range pc.PlatformTokens {
			if t.Token != token {
				kept = append(kept, t)
			}
		}
		pc.PlatformTokens = kept
		cr.Profiles[a.Profile.Name] = pc
		return false, true, nil
	})
}

// isPlatformIssuer uses RFC 8414 discovery to choose the login credential.
// Missing metadata keeps the CLI on project keys; other errors are returned.
func isPlatformIssuer(ctx context.Context, anon *api.Client) (bool, error) {
	var meta struct {
		Issuer string `json:"issuer"`
	}
	err := anon.Do(ctx, http.MethodGet, "/.well-known/oauth-authorization-server", nil, nil, &meta)
	if err == nil {
		return meta.Issuer != "", nil
	}
	if cerr.ExitCode(err) == cerr.ExitNotFound {
		return false, nil
	}
	return false, err
}
