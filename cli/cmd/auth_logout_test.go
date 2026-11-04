// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/config"
	"github.com/stretchr/testify/require"
)

const logoutKeyID = "88888888-8888-8888-8888-888888888888"

func seedLogoutKeys(t *testing.T, d, base string) *config.Store {
	t.Helper()
	oidcProfile(t, d, base, time.Hour)
	st, err := config.Open(d)
	require.NoError(t, err)
	require.NoError(t, st.Update(func(c *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles["default"]
		pc.WorkloadKeys = []config.WorkloadKey{
			{ProjectID: testProjP1, KeyID: logoutKeyID, Key: "owned", ExpiresAt: time.Now().Add(-time.Minute)}, // expired still gets a revoke opportunity
			{ProjectID: testProjP1, KeyID: "static-key", Key: "exchanged", Source: "static-source", APIURL: base},
			{ProjectID: testProjP1, KeyID: "foreign-key", Key: "foreign", APIURL: "https://other.example"},
		}
		pc.PlatformTokens = []config.PlatformToken{{ProjectID: testProjP1, Token: "platform"}}
		cr.Profiles["default"] = pc
		cr.Profiles["other"] = config.ProfileCredentials{Token: "other-profile", WorkloadKeys: pc.WorkloadKeys}
		c.Upsert(config.Profile{Name: "other", APIURL: base, Credential: "token"})
		return true, true, nil
	}))
	return st
}

func TestLogoutCommandUsesSavedLoginAndAPIRegardlessOfOverrides(t *testing.T) {
	for _, status := range []int{204, 403, 503} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			// Given login-owned, exchanged, foreign-origin and other-profile keys.
			f, override := newFakeAPI(t), newFakeAPI(t)
			d := cfgDir(t)
			st := seedLogoutKeys(t, d, f.srv.URL)
			f.handle("DELETE", "/projects/"+testProjP1+"/api-keys/"+logoutKeyID, func(w http.ResponseWriter, r *http.Request) {
				require.Equal(t, "Bearer jwt", r.Header.Get("Authorization"))
				// Local cleanup must precede network, and the store lock must be free.
				require.NoError(t, st.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
					require.NotContains(t, cr.Profiles, "default")
					return false, false, nil
				}))
				w.WriteHeader(status)
				if status != 204 {
					if _, err := w.Write([]byte(`{"code":"FORBIDDEN","source":"DAYTONA_API","message":"credential-SENSITIVE"}`)); err != nil {
						t.Error(err)
					}
				}
			})
			// When logout receives unrelated explicit bearer/API overrides.
			out, stderr, code := run(t, d, nil, "logout", "--api-key", "override-SENSITIVE", "--api-url", override.srv.URL, "--debug")
			// Then only the cached login-owned key is revoked with the saved login.
			require.Equal(t, 0, code, stderr)
			require.Empty(t, out)
			require.Len(t, f.requests, 1)
			require.Empty(t, override.requests)
			require.NotContains(t, stderr, "SENSITIVE")
			cr, err := st.LoadCredentials()
			require.NoError(t, err)
			require.NotContains(t, cr.Profiles, "default")
			require.Equal(t, "other-profile", cr.Profiles["other"].Token)
			cfg, err := st.LoadConfig()
			require.NoError(t, err)
			p, ok := cfg.Profile("default")
			require.True(t, ok)
			require.Empty(t, p.Credential)
		})
	}
}

func TestLogoutCommandBoundsTheWholeBatchAndStillClearsLocalState(t *testing.T) {
	// Given an API whose first revoke hangs and a second key needing cleanup.
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1); <-r.Context().Done() }))
	defer srv.Close()
	d := cfgDir(t)
	st := seedLogoutKeys(t, d, srv.URL)
	require.NoError(t, st.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles["default"]
		other := pc.WorkloadKeys[0]
		other.KeyID = "second-id"
		pc.WorkloadKeys = append(pc.WorkloadKeys, other)
		cr.Profiles["default"] = pc
		return false, true, nil
	}))
	// When logout attempts the batch under one three-second budget.
	start := time.Now()
	_, stderr, code := run(t, d, nil, "logout")
	// Then it does not spend another timeout per key, and local cleanup succeeds.
	require.Equal(t, 0, code, stderr)
	require.Less(t, time.Since(start), 5*time.Second)
	require.Equal(t, int32(1), calls.Load())
	cr, err := st.LoadCredentials()
	require.NoError(t, err)
	require.NotContains(t, cr.Profiles, "default")
}

func TestLogoutCommandNeverRevokesAManuallySuppliedProfileCredential(t *testing.T) {
	// Given a static profile and unrelated exchanged-key metadata.
	f := newFakeAPI(t)
	d := cfgDir(t)
	_, stderr, code := run(t, d, nil, "login", "--token", "manual", "--api-url", f.srv.URL)
	require.Equal(t, 0, code, stderr)
	// When that profile logs out.
	_, stderr, code = run(t, d, nil, "logout")
	// Then no server request, including a revoke of the supplied key, occurs.
	require.Equal(t, 0, code, stderr)
	require.Empty(t, f.requests)
}

func TestLogoutCommandRefreshesOnlyTheSavedLoginForRevocation(t *testing.T) {
	// Given cached keys and an expired saved access token with refresh capability.
	f := newFakeAPI(t)
	d := cfgDir(t)
	st := seedLogoutKeys(t, d, f.srv.URL)
	require.NoError(t, st.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles["default"]
		pc.OIDC.ExpiresAt = time.Now().Add(-time.Hour)
		pc.OIDC.ClientID = "native"
		cr.Profiles["default"] = pc
		return false, true, nil
	}))
	f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
	f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{"issuer": f.srv.URL + "/idp", "authorization_endpoint": f.srv.URL + "/idp/authorize", "token_endpoint": f.srv.URL + "/idp/token"})
	f.handle("POST", "/idp/token", func(w http.ResponseWriter, r *http.Request) {
		require.NoError(t, r.ParseForm())
		require.Equal(t, "rt", r.PostForm.Get("refresh_token"))
		require.Equal(t, "native", r.PostForm.Get("client_id"))
		if _, err := w.Write([]byte(`{"access_token":"refreshed-saved-login","refresh_token":"rotated","expires_in":3600}`)); err != nil {
			t.Error(err)
		}
	})
	f.handle("DELETE", "/projects/"+testProjP1+"/api-keys/"+logoutKeyID, func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "Bearer refreshed-saved-login", r.Header.Get("Authorization"))
		w.WriteHeader(204)
	})
	// When an explicit bearer override accompanies logout.
	_, stderr, code := run(t, d, nil, "logout", "--api-key", "must-not-be-sent")
	// Then refresh and revoke use the saved login, without persisting rotated tokens.
	require.Equal(t, 0, code, stderr)
	require.Len(t, f.requests, 4)
	cr, err := st.LoadCredentials()
	require.NoError(t, err)
	require.NotContains(t, cr.Profiles, "default")
}
