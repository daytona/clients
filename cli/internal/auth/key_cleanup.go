// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/daytona/clients/cli/internal/mgmt"
)

const keyCleanupTimeout = 3 * time.Second

// LoginKeyCleanup owns a snapshot of a saved login's revocable keys. Its zero
// value does nothing. It never consults a replacement session's credentials.
type LoginKeyCleanup struct {
	base, clientID string
	tokens         config.Tokens
	keys           []config.WorkloadKey
}

// SnapshotLoginKeys must be called under the store lock, before removing or
// replacing credentials. Keys without an API URL belong to the saved API,
// including its implicit default; invocation overrides do not prove their origin.
func SnapshotLoginKeys(profile config.Profile, pc config.ProfileCredentials) LoginKeyCleanup {
	base := strings.TrimRight(strings.TrimSpace(profile.APIURL), "/")
	if base == "" {
		base = app.DefaultAPIURL
	}
	snapshot := LoginKeyCleanup{base: base, clientID: profile.OIDCClientID}
	if pc.OIDC != nil {
		snapshot.tokens = *pc.OIDC
		for _, k := range pc.WorkloadKeys {
			if k.Source == "" && (k.APIURL == "" || k.APIURL == base) {
				snapshot.keys = append(snapshot.keys, k)
			}
		}
	}
	return snapshot
}

// revokeKeys gives the whole cleanup batch one budget, never one per key.
// Failures are diagnostics only, without server text that might expose secrets.
func revokeKeys(ctx context.Context, a *app.App, c *api.Client, keys []config.WorkloadKey) {
	ctx, cancel := context.WithTimeout(ctx, keyCleanupTimeout)
	defer cancel()
	for _, k := range keys {
		if ctx.Err() != nil {
			break
		}
		if k.KeyID == "" {
			continue
		}
		if err := mgmt.RevokeProjectKey(ctx, c, k.ProjectID, k.KeyID); err != nil && a.Globals.Debug {
			fmt.Fprintln(a.Stderr, "debug: could not revoke a CLI-minted project key; local cleanup continues")
		}
	}
}

// Logout clears only this profile's local session, then attempts revocation of
// its login-minted keys against the saved API. Keys an earlier release
// exchanged from a static credential are excluded: their source credential is
// not the login being signed out.
func Logout(ctx context.Context, a *app.App) error {
	var snapshot LoginKeyCleanup
	err := a.Store.Update(func(c *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles[a.Profile.Name]
		profile := config.Profile{Name: a.Profile.Name}
		if p, ok := c.Profile(a.Profile.Name); ok {
			profile = *p
			p.Credential = ""
		}
		snapshot = SnapshotLoginKeys(profile, pc)
		delete(cr.Profiles, a.Profile.Name)
		return true, true, nil
	})
	// Credentials may already have persisted even when the config write fails.
	// The snapshot is still our only chance to revoke those removed key IDs.
	snapshot.Revoke(ctx, a)
	return err
}

// DeleteProfile removes a profile and its saved credentials, then attempts
// revocation of its login-minted keys against the profile's saved API, like
// Logout. A missing profile is reported by notFound.
func DeleteProfile(ctx context.Context, a *app.App, name string, notFound func() error) error {
	var snapshot LoginKeyCleanup
	err := a.Store.Update(func(c *config.Config, cr *config.Credentials) (bool, bool, error) {
		p, ok := c.Profile(name)
		if !ok {
			return false, false, notFound()
		}
		snapshot = SnapshotLoginKeys(*p, cr.Profiles[name])
		c.Remove(name)
		delete(cr.Profiles, name)
		return true, true, nil
	})
	// Credentials are written before config, so a failed update may still
	// have removed them; revoke only when they are gone, never for a profile
	// that still holds its keys.
	if err != nil && !credentialsRemoved(a, name) {
		return err
	}
	snapshot.Revoke(ctx, a)
	return err
}

func credentialsRemoved(a *app.App, name string) bool {
	cr, err := a.Store.LoadCredentials()
	if err != nil {
		return false
	}
	_, ok := cr.Profiles[name]
	return !ok
}

// Revoke best-effort cleans up a replaced login outside the store lock, within
// one bounded budget for token refresh and all revocations combined.
func (s LoginKeyCleanup) Revoke(ctx context.Context, a *app.App) {
	if s.tokens.AccessToken == "" || len(s.keys) == 0 {
		return
	}
	base, bearer, clientID, tokens := s.base, s.tokens.AccessToken, s.clientID, s.tokens
	ctx, cancel := context.WithTimeout(ctx, keyCleanupTimeout)
	defer cancel()
	// Use only the snapshotted profile login, never a flag/env credential.
	// Refresh is inside the same overall cleanup budget and is never persisted
	// after session replacement. Local state has already been removed.
	if time.Until(tokens.ExpiresAt) <= refreshSkew && tokens.RefreshToken != "" {
		fresh, refreshErr := func() (*TokenSet, error) {
			anon := api.New(base, "", app.Version)
			sc, err := FetchServerConfig(ctx, anon)
			if err != nil {
				return nil, err
			}
			if tokens.ClientID != "" {
				clientID = tokens.ClientID
			}
			provider, err := ProviderFor(ctx, anon.HTTP, sc, clientID, a.Getenv)
			if err != nil {
				return nil, err
			}
			return Refresh(ctx, anon.HTTP, provider, tokens.RefreshToken)
		}()
		if refreshErr == nil {
			bearer = fresh.AccessToken
		} else if a.Globals.Debug {
			fmt.Fprintln(a.Stderr, "debug: could not refresh the saved login for key cleanup; local cleanup continues")
		}
	}
	revokeKeys(ctx, a, api.New(base, bearer, app.Version), s.keys)
}
