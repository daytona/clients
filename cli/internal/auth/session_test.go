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

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/stretchr/testify/require"
)

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
	a, err := app.Load(app.Globals{APIURL: apiURL, Profile: "default", ProfileMayBeNew: true}, io.Discard, io.Discard, getenv)
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
