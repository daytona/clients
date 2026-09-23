// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
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
