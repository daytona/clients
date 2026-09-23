// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"
	"encoding/base64"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/daytona/clients/cli/config"
	"golang.org/x/oauth2"
)

func jwtWithPayload(payload string) string {
	return "header." + base64.RawURLEncoding.EncodeToString([]byte(payload)) + ".signature"
}

func TestWorkOSConfigUsesTheIssuerHost(t *testing.T) {
	oauth2Config, err := WorkOSConfig(config.WorkOSClient{
		Issuer:   "https://auth.example.com/user_management/client_123",
		ClientId: "client_123",
	})
	if err != nil {
		t.Fatal(err)
	}

	if oauth2Config.Endpoint.AuthURL != "https://auth.example.com/user_management/authorize" {
		t.Errorf("AuthURL = %s", oauth2Config.Endpoint.AuthURL)
	}
	if oauth2Config.Endpoint.TokenURL != "https://auth.example.com/user_management/authenticate" {
		t.Errorf("TokenURL = %s", oauth2Config.Endpoint.TokenURL)
	}
	if oauth2Config.ClientID != "client_123" {
		t.Errorf("ClientID = %s", oauth2Config.ClientID)
	}
	if oauth2Config.Endpoint.AuthStyle != oauth2.AuthStyleInParams {
		t.Error("public client must send client_id in the request body")
	}
}

func TestWorkOSConfigRejectsAnIssuerWithoutHost(t *testing.T) {
	if _, err := WorkOSConfig(config.WorkOSClient{Issuer: "user_management/client_123"}); err == nil {
		t.Error("expected an error for an issuer without scheme and host")
	}
}

func TestNewTokenReadsExpiryFromTheAccessTokenWhenTheResponseHasNone(t *testing.T) {
	exp := time.Now().Add(15 * time.Minute).Truncate(time.Second)
	workos := &config.WorkOSClient{Issuer: "https://auth.example.com", ClientId: "client_123"}

	token, err := NewToken(&oauth2.Token{
		AccessToken:  jwtWithPayload(fmt.Sprintf(`{"exp":%d}`, exp.Unix())),
		RefreshToken: "refresh",
	}, workos)
	if err != nil {
		t.Fatal(err)
	}

	if !token.ExpiresAt.Equal(exp) {
		t.Errorf("ExpiresAt = %s, want %s", token.ExpiresAt, exp)
	}
	if token.WorkOS != workos || token.RefreshToken != "refresh" {
		t.Error("token must keep its refresh token and issuing provider")
	}
}

func TestNewTokenKeepsTheResponseExpiry(t *testing.T) {
	expiry := time.Now().Add(time.Hour)

	token, err := NewToken(&oauth2.Token{AccessToken: "opaque", Expiry: expiry}, nil)
	if err != nil {
		t.Fatal(err)
	}

	if !token.ExpiresAt.Equal(expiry) || token.WorkOS != nil {
		t.Errorf("unexpected token %+v", token)
	}
}

func TestNewTokenFailsWithoutAnyExpiry(t *testing.T) {
	for _, accessToken := range []string{"opaque", jwtWithPayload(`{}`), "a.!!!.c"} {
		if _, err := NewToken(&oauth2.Token{AccessToken: accessToken}, nil); err == nil {
			t.Errorf("expected an error for access token %q", accessToken)
		}
	}
}

func writeProfileWithToken(t *testing.T, token config.Token) {
	t.Helper()

	t.Setenv("DAYTONA_CONFIG_DIR", t.TempDir())
	t.Setenv(config.DAYTONA_API_URL_ENV_VAR, "")
	t.Setenv(config.DAYTONA_API_KEY_ENV_VAR, "")

	c := config.Config{
		ActiveProfileId: "default",
		Profiles: []config.Profile{{
			Id:   "default",
			Name: "default",
			Api:  config.ServerApi{Url: "https://api.example.test", Token: &token},
		}},
	}
	if err := c.Save(); err != nil {
		t.Fatal(err)
	}
}

func storedToken(t *testing.T) config.Token {
	t.Helper()

	c, err := config.GetConfig()
	if err != nil {
		t.Fatal(err)
	}
	profile, err := c.GetActiveProfile()
	if err != nil {
		t.Fatal(err)
	}

	return *profile.Api.Token
}

// refusingAuth0 stands in for Auth0 in tests that must never reach it: any request
// fails the test.
func refusingAuth0(t *testing.T) {
	t.Helper()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Errorf("Auth0 must not be contacted, got %s %s", r.Method, r.URL.Path)
		http.Error(w, "unexpected", http.StatusInternalServerError)
	}))
	t.Cleanup(server.Close)
	t.Setenv("DAYTONA_AUTH0_DOMAIN", server.URL)
	t.Setenv("DAYTONA_AUTH0_CLIENT_ID", "auth0-client")
}

func TestRefreshTokenIfNeededRefreshesAWorkOSTokenAgainstItsIssuer(t *testing.T) {
	refusingAuth0(t)
	newExp := time.Now().Add(time.Hour).Truncate(time.Second)

	var authenticateCalls atomic.Int32
	workos := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/user_management/authenticate" {
			t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
			http.NotFound(w, r)
			return
		}
		authenticateCalls.Add(1)
		if err := r.ParseForm(); err != nil {
			t.Error(err)
		}
		if _, _, ok := r.BasicAuth(); ok {
			t.Error("public client must not send Basic auth")
		}
		for key, want := range map[string]string{
			"grant_type":    "refresh_token",
			"client_id":     "client_123",
			"refresh_token": "old-refresh",
		} {
			if got := r.PostForm.Get(key); got != want {
				t.Errorf("form %s = %q, want %q", key, got, want)
			}
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = fmt.Fprintf(w, `{"access_token":%q,"refresh_token":"rotated-refresh","user":{"id":"user_1"}}`,
			jwtWithPayload(fmt.Sprintf(`{"exp":%d}`, newExp.Unix())))
	}))
	t.Cleanup(workos.Close)

	client := config.WorkOSClient{Issuer: workos.URL + "/user_management/client_123", ClientId: "client_123"}
	writeProfileWithToken(t, config.Token{
		AccessToken:  "expiring",
		RefreshToken: "old-refresh",
		ExpiresAt:    time.Now().Add(time.Minute),
		WorkOS:       &client,
	})

	if err := RefreshTokenIfNeeded(context.Background()); err != nil {
		t.Fatal(err)
	}

	if calls := authenticateCalls.Load(); calls != 1 {
		t.Errorf("authenticate calls = %d, want 1", calls)
	}
	token := storedToken(t)
	if token.RefreshToken != "rotated-refresh" {
		t.Errorf("stored refresh token = %q, want the rotated one", token.RefreshToken)
	}
	if token.AccessToken == "expiring" {
		t.Error("stored access token was not replaced")
	}
	if !token.ExpiresAt.Equal(newExp) {
		t.Errorf("stored ExpiresAt = %s, want %s from the access token", token.ExpiresAt, newExp)
	}
	if token.WorkOS == nil || *token.WorkOS != client {
		t.Errorf("stored WorkOS client = %+v, want %+v", token.WorkOS, client)
	}
}

func TestRefreshTokenIfNeededRefreshesAnAuth0TokenAgainstAuth0(t *testing.T) {
	var tokenCalls atomic.Int32
	var auth0 *httptest.Server
	auth0 = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/.well-known/openid-configuration":
			_, _ = fmt.Fprintf(w, `{"issuer":%q,"authorization_endpoint":%q,"token_endpoint":%q,"jwks_uri":%q}`,
				auth0.URL, auth0.URL+"/authorize", auth0.URL+"/oauth/token", auth0.URL+"/.well-known/jwks.json")
		case "/oauth/token":
			tokenCalls.Add(1)
			if err := r.ParseForm(); err != nil {
				t.Error(err)
			}
			if got := r.PostForm.Get("client_id"); got != "auth0-client" {
				t.Errorf("form client_id = %q", got)
			}
			if got := r.PostForm.Get("refresh_token"); got != "old-refresh" {
				t.Errorf("form refresh_token = %q", got)
			}
			_, _ = fmt.Fprint(w, `{"access_token":"fresh","refresh_token":"rotated-refresh","expires_in":86400,"token_type":"Bearer"}`)
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(auth0.Close)
	t.Setenv("DAYTONA_AUTH0_DOMAIN", auth0.URL)
	t.Setenv("DAYTONA_AUTH0_CLIENT_ID", "auth0-client")

	writeProfileWithToken(t, config.Token{
		AccessToken:  "expiring",
		RefreshToken: "old-refresh",
		ExpiresAt:    time.Now().Add(time.Minute),
	})

	if err := RefreshTokenIfNeeded(context.Background()); err != nil {
		t.Fatal(err)
	}

	if calls := tokenCalls.Load(); calls != 1 {
		t.Errorf("token endpoint calls = %d, want 1", calls)
	}
	token := storedToken(t)
	if token.AccessToken != "fresh" || token.RefreshToken != "rotated-refresh" {
		t.Errorf("stored token = %+v", token)
	}
	if token.WorkOS != nil {
		t.Error("an Auth0 token must stay an Auth0 token")
	}
	if remaining := time.Until(token.ExpiresAt); remaining < 23*time.Hour || remaining > 24*time.Hour {
		t.Errorf("stored ExpiresAt %s does not reflect expires_in", token.ExpiresAt)
	}
}

func TestRefreshTokenIfNeededLeavesAFreshTokenAlone(t *testing.T) {
	refusingAuth0(t)
	original := config.Token{
		AccessToken:  "fresh",
		RefreshToken: "refresh",
		ExpiresAt:    time.Now().Add(time.Hour).Truncate(time.Second),
		WorkOS:       &config.WorkOSClient{Issuer: "http://127.0.0.1:1/user_management/client_123", ClientId: "client_123"},
	}
	writeProfileWithToken(t, original)

	if err := RefreshTokenIfNeeded(context.Background()); err != nil {
		t.Fatal(err)
	}

	token := storedToken(t)
	if token.AccessToken != original.AccessToken || token.RefreshToken != original.RefreshToken || !token.ExpiresAt.Equal(original.ExpiresAt) {
		t.Errorf("stored token changed to %+v", token)
	}
}

func TestRefreshTokenIfNeededAsksForLoginOnlyWhenTheSessionIsDead(t *testing.T) {
	for name, tc := range map[string]struct {
		status      int
		contentType string
		body        string
		wantLogin   bool
	}{
		"invalid_grant": {http.StatusBadRequest, "application/json", `{"error":"invalid_grant","error_description":"Invalid refresh token."}`, true},
		"server_error":  {http.StatusInternalServerError, "application/json", `{"error":"server_error"}`, false},
		"gateway error": {http.StatusBadGateway, "text/html", `<h1>upstream unavailable</h1>`, false},
	} {
		t.Run(name, func(t *testing.T) {
			refusingAuth0(t)
			workos := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", tc.contentType)
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(tc.body))
			}))
			t.Cleanup(workos.Close)

			writeProfileWithToken(t, config.Token{
				AccessToken:  "expiring",
				RefreshToken: "old-refresh",
				ExpiresAt:    time.Now().Add(time.Minute),
				WorkOS:       &config.WorkOSClient{Issuer: workos.URL + "/user_management/client_123", ClientId: "client_123"},
			})

			err := RefreshTokenIfNeeded(context.Background())
			if err == nil {
				t.Fatal("expected an error")
			}
			if gotLogin := strings.Contains(err.Error(), "daytona login"); gotLogin != tc.wantLogin {
				t.Errorf("error %q: asks for login = %v, want %v", err, gotLogin, tc.wantLogin)
			}
			if token := storedToken(t); token.RefreshToken != "old-refresh" {
				t.Error("a failed refresh must not touch the stored token")
			}
		})
	}
}

func TestWorkOSConfigRequiresHttpsOffLoopback(t *testing.T) {
	for issuer, wantErr := range map[string]bool{
		"http://auth.example.com/user_management/client_123":  true,
		"https://auth.example.com/user_management/client_123": false,
		"http://localhost:3001/user_management/client_123":    false,
		"http://127.0.0.1:3001/user_management/client_123":    false,
		"http://[::1]:3001/user_management/client_123":        false,
		"ftp://localhost/user_management/client_123":          true,
	} {
		_, err := WorkOSConfig(config.WorkOSClient{Issuer: issuer, ClientId: "client_123"})
		if (err != nil) != wantErr {
			t.Errorf("WorkOSConfig(%q) error = %v, want error %v", issuer, err, wantErr)
		}
	}
}

func callbackServerOnFreePort(t *testing.T, state string) (<-chan string, <-chan error, string) {
	t.Helper()

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := fmt.Sprint(listener.Addr().(*net.TCPAddr).Port)
	_ = listener.Close()
	t.Setenv("DAYTONA_AUTH0_CALLBACK_PORT", port)

	codes := make(chan string, 1)
	errs := make(chan error, 1)
	go func() {
		code, err := StartCallbackServer(state)
		codes <- code
		errs <- err
	}()

	callback := "http://127.0.0.1:" + port + "/callback"
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		conn, err := net.Dial("tcp", "127.0.0.1:"+port)
		if err == nil {
			_ = conn.Close()
			return codes, errs, callback
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("callback server did not start listening")
	return nil, nil, ""
}

func TestStartCallbackServerReportsTheProviderError(t *testing.T) {
	codes, errs, callback := callbackServerOnFreePort(t, "expected-state")

	response, err := http.Get(callback + "?state=expected-state&error=access_denied&error_description=Login+denied+by+policy")
	if err != nil {
		t.Fatal(err)
	}
	_ = response.Body.Close()

	if got := <-errs; got == nil || !strings.Contains(got.Error(), "access_denied: Login denied by policy") {
		t.Errorf("error = %v, want the provider's error and description", got)
	}
	if code := <-codes; code != "" {
		t.Errorf("code = %q, want none", code)
	}
	if response.StatusCode != http.StatusUnauthorized {
		t.Errorf("status = %d, want 401", response.StatusCode)
	}
}

func TestStartCallbackServerRejectsAForeignState(t *testing.T) {
	_, errs, callback := callbackServerOnFreePort(t, "expected-state")

	response, err := http.Get(callback + "?state=other&code=abc")
	if err != nil {
		t.Fatal(err)
	}
	_ = response.Body.Close()

	if got := <-errs; got == nil || !strings.Contains(got.Error(), "invalid state") {
		t.Errorf("error = %v, want an invalid state error", got)
	}
}

func TestStartCallbackServerReturnsTheCode(t *testing.T) {
	codes, errs, callback := callbackServerOnFreePort(t, "expected-state")

	response, err := http.Get(callback + "?state=expected-state&code=the-code")
	if err != nil {
		t.Fatal(err)
	}
	_ = response.Body.Close()

	if err := <-errs; err != nil {
		t.Fatal(err)
	}
	if code := <-codes; code != "the-code" {
		t.Errorf("code = %q", code)
	}
}
