// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/daytona/clients/cli/config"
)

func serveConfig(t *testing.T, status int, body string) string {
	t.Helper()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/config" {
			http.NotFound(w, r)
			return
		}
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}))
	t.Cleanup(server.Close)

	return server.URL + "/api"
}

func TestFetchOidcConfigReadsTheAdvertisedLogin(t *testing.T) {
	apiUrl := serveConfig(t, http.StatusOK, `{"version":"x","oidc":{"issuer":"https://auth.example.com/user_management/client_123","clientId":"client_123","audience":"a","provider":"workos"}}`)

	advertised, err := fetchOidcConfig(context.Background(), apiUrl+"/")
	if err != nil {
		t.Fatal(err)
	}

	want := oidcConfig{Issuer: "https://auth.example.com/user_management/client_123", ClientId: "client_123", Provider: workosProvider}
	if advertised != want {
		t.Errorf("got %+v, want %+v", advertised, want)
	}
}

func TestFetchOidcConfigTreatsAMissingProviderAsAuth0(t *testing.T) {
	apiUrl := serveConfig(t, http.StatusOK, `{"oidc":{"issuer":"https://tenant.auth0.com/","clientId":"abc","audience":"a"}}`)

	advertised, err := fetchOidcConfig(context.Background(), apiUrl)
	if err != nil {
		t.Fatal(err)
	}

	if advertised.Provider == workosProvider {
		t.Error("an API without a provider field must not select WorkOS")
	}
}

func TestFetchOidcConfigFailsOnAnErrorStatus(t *testing.T) {
	apiUrl := serveConfig(t, http.StatusServiceUnavailable, `{}`)

	if _, err := fetchOidcConfig(context.Background(), apiUrl); err == nil {
		t.Error("expected an error for a non-200 response")
	}
}

func writeConfig(t *testing.T, c config.Config) {
	t.Helper()

	t.Setenv("DAYTONA_CONFIG_DIR", t.TempDir())
	t.Setenv(config.DAYTONA_API_URL_ENV_VAR, "")
	t.Setenv(config.DAYTONA_API_KEY_ENV_VAR, "")
	if err := c.Save(); err != nil {
		t.Fatal(err)
	}
}

func TestLoginApiUrlUsesTheActiveProfile(t *testing.T) {
	writeConfig(t, config.Config{
		ActiveProfileId: "staging",
		Profiles: []config.Profile{
			{Id: "prod", Name: "prod", Api: config.ServerApi{Url: "https://prod.example.test/api"}},
			{Id: "staging", Name: "staging", Api: config.ServerApi{Url: "https://staging.example.test/api"}},
		},
	})

	apiUrl, err := loginApiUrl()
	if err != nil {
		t.Fatal(err)
	}
	if apiUrl != "https://staging.example.test/api" {
		t.Errorf("apiUrl = %s", apiUrl)
	}
}

func TestLoginApiUrlFallsBackToTheDefaultWithoutProfiles(t *testing.T) {
	writeConfig(t, config.Config{})

	apiUrl, err := loginApiUrl()
	if err != nil {
		t.Fatal(err)
	}
	if apiUrl != defaultApiUrl() {
		t.Errorf("apiUrl = %s, want %s", apiUrl, defaultApiUrl())
	}
}

func TestLoginApiUrlFailsBeforeTheBrowserOpensWhenNoProfileIsActive(t *testing.T) {
	writeConfig(t, config.Config{
		Profiles: []config.Profile{{Id: "prod", Name: "prod", Api: config.ServerApi{Url: "https://prod.example.test/api"}}},
	})

	if _, err := loginApiUrl(); !errors.Is(err, config.ErrNoActiveProfile) {
		t.Errorf("error = %v, want ErrNoActiveProfile", err)
	}
}
