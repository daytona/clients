// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/stretchr/testify/require"
)

// `--token ""` (an unset CI secret expanded into the flag) must fail fast
// instead of falling through to the interactive login and polling.
func TestLoginRejectsEmptyTokenFlag(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	for _, value := range []string{"", "   "} {
		_, errb, code := run(t, d, nil, "login", "--token", value, "--api-url", f.srv.URL)
		require.Equal(t, cerr.ExitUsage, code, errb)
		require.Contains(t, errb, "--token")
	}
	require.Empty(t, f.requests, "an empty --token must not start any login flow")
	st, err := config.Open(d)
	require.NoError(t, err)
	cr, err := st.LoadCredentials()
	require.NoError(t, err)
	require.Empty(t, cr.Profiles)
}

// Deleting an OIDC profile revokes its cached login-minted project keys
// against the profile's saved API, after the store lock is released, and
// leaves foreign-origin and other profiles' keys alone. A key an earlier
// release exchanged from a static credential is dropped by the write.
func TestProfileDeleteRevokesLoginMintedKeys(t *testing.T) {
	f, override := newFakeAPI(t), newFakeAPI(t)
	d := cfgDir(t)
	st := seedLogoutKeys(t, d, f.srv.URL)
	revoked := 0
	f.handle("DELETE", "/projects/"+testProjP1+"/api-keys/"+logoutKeyID, func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "Bearer jwt", r.Header.Get("Authorization"))
		require.NoError(t, st.Update(func(c *config.Config, cr *config.Credentials) (bool, bool, error) {
			require.NotContains(t, cr.Profiles, "default")
			_, ok := c.Profile("default")
			require.False(t, ok)
			return false, false, nil
		}))
		revoked++
		w.WriteHeader(http.StatusNoContent)
	})

	_, stderr, code := run(t, d, nil, "profile", "delete", "default", "--yes", "--api-key", "override-SENSITIVE", "--api-url", override.srv.URL)

	require.Equal(t, 0, code, stderr)
	require.Equal(t, 1, revoked)
	require.Len(t, f.requests, 1)
	require.Empty(t, override.requests)
	cr, err := st.LoadCredentials()
	require.NoError(t, err)
	require.NotContains(t, cr.Profiles, "default")
	require.Equal(t, "other-profile", cr.Profiles["other"].Token)
	var otherKeys []string
	for _, k := range cr.Profiles["other"].WorkloadKeys {
		otherKeys = append(otherKeys, k.KeyID)
	}
	require.Equal(t, []string{logoutKeyID, "foreign-key"}, otherKeys)
}

// A platform token is a credential of the API that minted it: an invocation
// against another API (--api-url) never sends it there.
func TestPlatformTokenIsNotReusedForAnotherAPIURL(t *testing.T) {
	saved, other := newFakeAPI(t), newFakeAPI(t)
	d := cfgDir(t)
	oidcProfile(t, d, saved.srv.URL, time.Hour)
	st, err := config.Open(d)
	require.NoError(t, err)
	require.NoError(t, st.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles["default"]
		pc.PlatformTokens = []config.PlatformToken{{ProjectID: testProjP1, Token: "pt_saved", ExpiresAt: time.Now().Add(time.Hour), APIURL: saved.srv.URL}}
		cr.Profiles["default"] = pc
		return false, true, nil
	}))
	// When: a URL override would otherwise reuse the saved login.
	_, errb, code := run(t, d, nil, "sandbox", "list", "-o", "json", "--api-url", other.srv.URL)

	// Then: refuse before discovery, exchange, refresh, or any API request.
	require.Equal(t, cerr.ExitUsage, code, errb)
	require.Empty(t, saved.requests)
	require.Empty(t, other.requests)
	cr, err := st.LoadCredentials()
	require.NoError(t, err)
	byURL := map[string]string{}
	for _, tok := range cr.Profiles["default"].PlatformTokens {
		byURL[tok.APIURL] = tok.Token
	}
	require.Equal(t, map[string]string{saved.srv.URL: "pt_saved"}, byURL)

	// The saved API still gets its own cached token back.
	saved.on("GET", "/regions", 200, map[string]any{"regions": []any{}, "default_region_id": nil})
	saved.handle("GET", "/sandboxes", func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "Bearer pt_saved", r.Header.Get("Authorization"))
		_, _ = w.Write([]byte(`{"sandboxes":[],"next_cursor":null}`))
	})
	_, errb, code = run(t, d, nil, "sandbox", "list", "-o", "json")
	require.Equal(t, 0, code, errb)
}
