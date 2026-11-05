// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"
	"time"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/google/uuid"
)

// AccessToken snapshots under the lock, refreshes outside it, then reconciles.
// A concurrent token rotation wins, including when our consumed refresh fails.
func AccessToken(ctx context.Context, a *app.App) (string, error) {
	var out, refreshToken, mintedBy, session string
	var snapshotToken *config.Tokens
	err := a.Store.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles[a.Profile.Name]
		if pc.OIDC == nil {
			return false, false, cerr.New(cerr.ExitAuth, "not logged in").WithHint("run `daytona login`")
		}
		if time.Until(pc.OIDC.ExpiresAt) > refreshSkew {
			out = pc.OIDC.AccessToken
			return false, false, nil
		}
		if pc.OIDC.RefreshToken == "" {
			return false, false, cerr.New(cerr.ExitAuth, "session expired").WithHint("run `daytona login` again")
		}
		changed := pc.KeySession == ""
		if changed {
			pc.KeySession = uuid.NewString()
			cr.Profiles[a.Profile.Name] = pc
		}
		refreshToken, mintedBy, session = pc.OIDC.RefreshToken, pc.OIDC.ClientID, pc.KeySession
		snapshot := *pc.OIDC
		snapshotToken = &snapshot
		return false, changed, nil
	})
	if err != nil || snapshotToken == nil {
		return out, err
	}
	anon := a.AnonClient()
	var fresh *config.Tokens
	refreshErr := func() error {
		sc, err := FetchServerConfig(ctx, anon)
		if err != nil {
			return err
		}
		override := mintedBy
		if override == "" {
			override = a.Profile.OIDCClientID
		}
		p, err := ProviderFor(ctx, anon.HTTP, sc, override, a.Getenv)
		if err != nil {
			return err
		}
		ts, err := Refresh(ctx, anon.HTTP, p, refreshToken)
		if err != nil {
			return err
		}
		fresh = &config.Tokens{AccessToken: ts.AccessToken, RefreshToken: ts.RefreshToken, IDToken: ts.IDToken, ExpiresAt: ts.ExpiresAt(time.Now()), ClientID: p.ClientID}
		return nil
	}()
	// A rejected refresh may mean another process redeemed the same refresh
	// token and has not saved its tokens yet: recheck the store for a bounded
	// time before reporting the session as expired.
	rejected := refreshErr != nil && cerr.ExitCode(refreshErr) == cerr.ExitAuth
	deadline := time.Now().Add(refreshRotationWait)
	for {
		recheck := false
		err = a.Store.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
			pc := cr.Profiles[a.Profile.Name]
			if pc.OIDC == nil || pc.KeySession != session {
				return false, false, cerr.New(cerr.ExitAuth, "not logged in").WithHint("run `daytona login`")
			}
			rotated := pc.OIDC.RefreshToken != snapshotToken.RefreshToken || pc.OIDC.AccessToken != snapshotToken.AccessToken
			if rotated && time.Until(pc.OIDC.ExpiresAt) > refreshSkew {
				out = pc.OIDC.AccessToken
				return false, false, nil
			}
			if refreshErr != nil {
				if rejected && !rotated && time.Now().Before(deadline) {
					recheck = true
					return false, false, nil
				}
				return false, false, refreshErr
			}
			pc.OIDC = fresh
			cr.Profiles[a.Profile.Name] = pc
			out = fresh.AccessToken
			return false, true, nil
		})
		if err != nil || !recheck {
			return out, err
		}
		select {
		case <-ctx.Done():
			return "", cerr.FromContext(ctx)
		case <-time.After(refreshRotationPoll):
		}
	}
}

// refreshRotationWait bounds how long a rejected refresh waits for a
// concurrent process to save the tokens it rotated; refreshRotationPoll is
// how often the store is rechecked meanwhile.
var (
	refreshRotationWait = time.Second
	refreshRotationPoll = 50 * time.Millisecond
)
