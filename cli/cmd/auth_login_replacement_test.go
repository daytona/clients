// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"io"
	"net/http"
	"sync"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/auth"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/stretchr/testify/require"
)

func replacementLoginAPI(t *testing.T) *fakeAPI {
	t.Helper()
	f := newFakeAPI(t)
	f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
	f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{
		"issuer": f.srv.URL + "/idp", "authorization_endpoint": f.srv.URL + "/idp/authorize",
		"token_endpoint": f.srv.URL + "/idp/token", "device_authorization_endpoint": f.srv.URL + "/idp/device",
	})
	f.on("POST", "/idp/device", 200, map[string]any{"device_code": "dc", "user_code": "ABCD", "verification_uri": "https://idp/activate", "expires_in": 60, "interval": 1})
	f.on("POST", "/idp/token", 200, map[string]any{"access_token": "jwt-new", "refresh_token": "rt-new", "expires_in": 3600})
	f.on("GET", "/me", 200, map[string]string{"id": "00000000-0000-0000-0000-00000000beef", "email": "new@example.com"})
	f.on("GET", "/orgs", 200, map[string]any{"organizations": []any{}})
	return f
}

func TestLogin_revokesPriorKeys_whenTokensArePublished(t *testing.T) {
	for _, defaultOrigin := range []bool{false, true} {
		t.Run(map[bool]string{false: "same-origin", true: "omitted-default-origin"}[defaultOrigin], func(t *testing.T) {
			// Given a valid login key and a replacement device login.
			f := replacementLoginAPI(t)
			d := cfgDir(t)
			oldBase := f.srv.URL
			if defaultOrigin {
				oldBase = ""
			}
			oidcProfile(t, d, oldBase, time.Hour)
			st, err := config.Open(d)
			require.NoError(t, err)
			require.NoError(t, st.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
				pc := cr.Profiles["default"]
				pc.KeySession = "old-session"
				pc.WorkloadKeys = []config.WorkloadKey{{ProjectID: testProjP1, KeyID: "old-id", Key: "old-key", ExpiresAt: time.Now().Add(time.Hour)}}
				cr.Profiles["default"] = pc
				return false, true, nil
			}))
			// Avoid contacting the real default API during cleanup in this fixture.
			if defaultOrigin {
				require.NoError(t, st.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
					pc := cr.Profiles["default"]
					pc.OIDC.AccessToken = ""
					return false, true, nil
				}))
			}
			f.handle("GET", "/me", func(w http.ResponseWriter, _ *http.Request) {
				getenv := func(key string) string {
					if key == "DAYTONA_CONFIG_DIR" {
						return d
					}
					return ""
				}
				a, err := app.Load(app.Globals{}, io.Discard, io.Discard, getenv)
				require.NoError(t, err)
				auth.Install(a)
				auth.InstallWorkload(a)
				require.Empty(t, a.CachedWorkloadKeyFn(testProjP1), "new tokens must never see the previous login's cached key")
				_, _ = io.WriteString(w, `{"id":"00000000-0000-0000-0000-00000000beef","email":"new@example.com"}`)
			})

			// When login publishes tokens before account discovery completes.
			_, stderr, code := run(t, d, nil, "login", "--device", "--api-url", f.srv.URL)

			// Then login succeeds without exposing the previous identity's key.
			require.Equal(t, 0, code, stderr)
		})
	}
}

func TestLogin_revokesOldKeysWithOldCredential_whenAccountIsReplaced(t *testing.T) {
	// Given a saved old API/session, plus a login against another API.
	old, next := newFakeAPI(t), replacementLoginAPI(t)
	d := cfgDir(t)
	oidcProfile(t, d, old.srv.URL, time.Hour)
	st, err := config.Open(d)
	require.NoError(t, err)
	require.NoError(t, st.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles["default"]
		pc.WorkloadKeys = []config.WorkloadKey{
			{ProjectID: testProjP1, KeyID: "old-id", Key: "old-key", ExpiresAt: time.Now().Add(time.Hour)},
			{ProjectID: testProjP1, KeyID: "static-id", Source: "static-source"},
			{ProjectID: testProjP1, KeyID: "foreign-id", APIURL: "https://other.example"},
		}
		cr.Profiles["default"] = pc
		return false, true, nil
	}))
	var mu sync.Mutex
	var revoked []string
	old.handle("DELETE", "/projects/"+testProjP1+"/api-keys/old-id", func(w http.ResponseWriter, r *http.Request) {
		// Cleanup must not hold the store lock or use the new JWT/explicit key.
		require.NoError(t, st.Update(func(_ *config.Config, _ *config.Credentials) (bool, bool, error) { return false, false, nil }))
		mu.Lock()
		revoked = append(revoked, r.Header.Get("Authorization"))
		mu.Unlock()
		w.WriteHeader(http.StatusNoContent)
	})

	// When a new identity replaces that saved login.
	_, stderr, code := run(t, d, nil, "login", "--device", "--api-url", next.srv.URL, "--api-key", "explicit-override")

	// Then only old-origin login keys are revoked, under the old JWT.
	require.Equal(t, 0, code, stderr)
	mu.Lock()
	got := append([]string(nil), revoked...)
	mu.Unlock()
	require.Equal(t, []string{"Bearer jwt"}, got)
}
