// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/stretchr/testify/require"
)

func TestExchangeStaticForProjectKeyCachesServerExpiry(t *testing.T) {
	t.Parallel()
	const (
		orgID     = "11111111-1111-1111-1111-111111111111"
		projectID = "22222222-2222-2222-2222-222222222222"
	)
	serverExpiry := time.Now().Add(5 * time.Minute).UTC().Truncate(time.Second)
	apiServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/orgs":
			require.Equal(t, http.MethodGet, r.Method)
			w.Header().Set("Content-Type", "application/json")
			require.NoError(t, json.NewEncoder(w).Encode(map[string]any{"organizations": []map[string]any{{
				"id":        orgID,
				"name":      "Acme",
				"personal":  false,
				"suspended": false,
				"membership": map[string]any{
					"default_project_id":            projectID,
					"roles":                         []string{"admin"},
					"permissions":                   []string{"read:organization"},
					"grantable_project_permissions": []string{"write:sandboxes"},
				},
			}}}))
		case "/projects/" + projectID + "/api-keys":
			require.Equal(t, http.MethodPost, r.Method)
			defer r.Body.Close()
			var req gen.CreateProjectApiKeyRequest
			require.NoError(t, json.NewDecoder(r.Body).Decode(&req))
			require.NotNil(t, req.ExpiresAt)
			w.Header().Set("Content-Type", "application/json")
			require.NoError(t, json.NewEncoder(w).Encode(map[string]any{
				"id":                  "33333333-3333-3333-3333-333333333333",
				"token":               "minted-key",
				"name":                "cli-default-host-abcdef",
				"project_permissions": []string{"write:sandboxes"},
				"expires_at":          serverExpiry.Format(time.RFC3339),
			}))
		default:
			t.Fatalf("unexpected path %s", r.URL.Path)
		}
	}))
	defer apiServer.Close()

	a := newSessionTestApp(t, apiServer.URL)
	a.Profile.OrgID = orgID

	key, err := exchangeStaticForProjectKey(context.Background(), a, apiServer.URL, "static-bearer", projectID, "")
	require.NoError(t, err)
	require.Equal(t, "minted-key", key)

	creds, err := a.Store.LoadCredentials()
	require.NoError(t, err)
	keys := creds.Profiles[a.Profile.Name].WorkloadKeys
	require.Len(t, keys, 1)
	require.Equal(t, key, keys[0].Key)
	require.WithinDuration(t, serverExpiry, keys[0].ExpiresAt, time.Second)
}

func TestProviderForSelectsAuthKitOnlyForWorkOS(t *testing.T) {
	t.Parallel()
	idp := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "/.well-known/openid-configuration", r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		require.NoError(t, json.NewEncoder(w).Encode(map[string]string{
			"issuer":                 "http://" + r.Host,
			"authorization_endpoint": "http://" + r.Host + "/authorize",
			"token_endpoint":         "http://" + r.Host + "/token",
		}))
	}))
	defer idp.Close()
	cli := "native"
	for _, tc := range []struct {
		provider gen.OidcProvider
		authKit  bool
	}{{gen.Workos, true}, {gen.Oidc, false}} {
		sc := &ServerConfig{Oidc: &gen.OidcPublicConfig{Provider: tc.provider, Issuer: idp.URL, CliClientId: &cli, Scope: "openid"}}
		p, err := ProviderFor(context.Background(), idp.Client(), sc, "", func(string) string { return "" })
		require.NoError(t, err)
		require.Equal(t, tc.authKit, p.AuthKit, "provider %q", tc.provider)
		require.Equal(t, "native", p.ClientID)
		if tc.authKit {
			require.Equal(t, idp.URL+"/authorize/device", p.DeviceAuthorizationEndpoint)
		} else {
			require.Empty(t, p.DeviceAuthorizationEndpoint, "a generic issuer's device grant comes only from discovery")
		}
	}
}

func newSessionTestApp(t *testing.T, apiURL string) *app.App {
	t.Helper()
	configDir := t.TempDir()
	getenv := func(key string) string {
		switch key {
		case "DAYTONA_CONFIG_DIR":
			return configDir
		default:
			return ""
		}
	}
	a, err := app.Load(app.Globals{APIURL: apiURL, Profile: "default"}, io.Discard, io.Discard, getenv)
	require.NoError(t, err)
	require.NoError(t, a.Store.Update(func(cfg *config.Config, creds *config.Credentials) (bool, bool, error) {
		cfg.Upsert(config.Profile{Name: "default", APIURL: apiURL})
		if creds.Profiles == nil {
			creds.Profiles = map[string]config.ProfileCredentials{}
		}
		creds.Profiles["default"] = config.ProfileCredentials{}
		return true, true, nil
	}))
	return a
}
