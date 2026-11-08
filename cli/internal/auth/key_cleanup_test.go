// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/stretchr/testify/require"
)

func TestReplacementSucceeds_whenSupersededKeyRevocationFails(t *testing.T) {
	// Given an expired cached key whose server refuses revocation.
	f := &lifecycleAPI{}
	var attempts atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodDelete {
			attempts.Add(1)
			http.Error(w, "sensitive-server-detail", http.StatusForbidden)
			return
		}
		f.serve(w, r)
	}))
	defer server.Close()
	a := lifecycleApp(t, server.URL)
	var diagnostics bytes.Buffer
	a.Globals.Debug = true
	a.Stderr = &diagnostics
	require.NoError(t, a.Store.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles[a.Profile.Name]
		pc.WorkloadKeys = []config.WorkloadKey{{
			ProjectID: lifecycleProject, KeyID: "superseded", Key: "old",
			APIURL: server.URL, ExpiresAt: time.Now().Add(-time.Hour),
		}}
		cr.Profiles[a.Profile.Name] = pc
		return false, true, nil
	}))

	// When the CLI replaces that key using the login's credential.
	key, err := WorkloadCredential(t.Context(), a, lifecycleProject, "")

	// Then failed cleanup does not discard the replacement or leak server text.
	require.NoError(t, err)
	require.Equal(t, "minted-1", key)
	require.Equal(t, int32(1), attempts.Load())
	require.NotEmpty(t, diagnostics.String())
	require.NotContains(t, diagnostics.String(), "sensitive-server-detail")
}

func TestLoginKeySnapshot_usesDefaultOrigin_whenSavedURLIsUnset(t *testing.T) {
	for _, saved := range []string{"", "  ", "///"} {
		t.Run(saved, func(t *testing.T) {
			// Given keys whose saved profile implicitly uses the default API.
			pc := config.ProfileCredentials{
				OIDC: &config.Tokens{AccessToken: "old-jwt"},
				WorkloadKeys: []config.WorkloadKey{
					{KeyID: "profile-origin"},
					{KeyID: "default", APIURL: app.DefaultAPIURL},
					{KeyID: "foreign", APIURL: "https://other.example"},
					{KeyID: "static", Source: "source"},
				},
			}

			// When snapshotting cleanup before a profile origin changes.
			snapshot := SnapshotLoginKeys(config.Profile{APIURL: saved}, pc)

			// Then revocation is routed only to the resolved saved API.
			require.Equal(t, app.DefaultAPIURL, snapshot.base)
			require.Len(t, snapshot.keys, 2)
			require.Equal(t, "profile-origin", snapshot.keys[0].KeyID)
			require.Equal(t, "default", snapshot.keys[1].KeyID)
		})
	}
}
