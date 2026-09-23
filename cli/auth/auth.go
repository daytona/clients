// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"
	"crypto/rand"
	_ "embed"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/coreos/go-oidc/v3/oidc"
	"github.com/daytona/clients/cli/config"
	log "github.com/sirupsen/logrus"
	"golang.org/x/oauth2"
)

//go:embed auth_success.html
var successHTML []byte

func StartCallbackServer(expectedState string) (string, error) {
	var code string
	var err error
	var wg sync.WaitGroup
	wg.Add(1)

	mux := http.NewServeMux()
	server := &http.Server{Addr: fmt.Sprintf(":%s", config.GetAuth0CallbackPort()), Handler: mux}

	mux.HandleFunc("/callback", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("state") != expectedState {
			err = fmt.Errorf("invalid state parameter")
			http.Error(w, "State invalid", http.StatusBadRequest)
			wg.Done()
			return
		}

		// An identity provider that refuses the login (e.g. an Auth0 post-login deny)
		// redirects back with an error and no code; show its reason instead of "no code".
		if providerError := r.URL.Query().Get("error"); providerError != "" {
			description := r.URL.Query().Get("error_description")
			err = fmt.Errorf("%s: %s", providerError, description)
			http.Error(w, description, http.StatusUnauthorized)
			wg.Done()
			return
		}

		code = r.URL.Query().Get("code")
		if code == "" {
			err = fmt.Errorf("no code in callback")
			http.Error(w, "No code", http.StatusBadRequest)
			wg.Done()
			return
		}

		w.Header().Set("Content-Type", "text/html")
		_, _ = w.Write(successHTML)

		// Delay server close to ensure browser receives the success page
		go func() {
			time.Sleep(500 * time.Millisecond)
			wg.Done()
			server.Close()
		}()
	})

	go func() {
		if err := server.ListenAndServe(); err != http.ErrServerClosed {
			log.Errorf("HTTP server error: %v", err)
		}
	}()
	wg.Wait()
	// Release the port on the error paths too, so a retried login can bind it again.
	_ = server.Close()

	if err != nil {
		return "", err
	}
	return code, nil
}

func GenerateRandomState() (string, error) {
	b := make([]byte, 32)
	_, err := rand.Read(b)
	if err != nil {
		return "", err
	}

	return base64.URLEncoding.EncodeToString(b), nil
}

// callbackURL is the redirect URI registered with both identity providers.
func callbackURL() string {
	return fmt.Sprintf("http://localhost:%s/callback", config.GetAuth0CallbackPort())
}

// Auth0Config is the Auth0 public client, built in at release time. Public clients send
// client_id in the token-request body; forcing it keeps oauth2 from probing Basic auth
// first and burning the single-use code or the rotating refresh token on a failed retry.
func Auth0Config(ctx context.Context) (oauth2.Config, *oidc.Provider, error) {
	provider, err := oidc.NewProvider(ctx, config.GetAuth0Domain())
	if err != nil {
		return oauth2.Config{}, nil, fmt.Errorf("failed to initialize OIDC provider: %w", err)
	}

	endpoint := provider.Endpoint()
	endpoint.AuthStyle = oauth2.AuthStyleInParams

	return oauth2.Config{
		ClientID:    config.GetAuth0ClientId(),
		RedirectURL: callbackURL(),
		Endpoint:    endpoint,
		Scopes:      []string{oidc.ScopeOpenID, oidc.ScopeOfflineAccess, "profile"},
	}, provider, nil
}

/*
WorkOSConfig is the WorkOS AuthKit User Management surface, the one the Daytona
dashboard logs in through. The API accepts a WorkOS token only when its client_id
equals the advertised client id and its iss equals the advertised issuer exactly,
and WorkOS derives iss from the host that served /user_management/authenticate,
so both endpoints live on the issuer's host.
*/
func WorkOSConfig(client config.WorkOSClient) (oauth2.Config, error) {
	issuer, err := url.Parse(client.Issuer)
	if err != nil || issuer.Scheme == "" || issuer.Host == "" {
		return oauth2.Config{}, fmt.Errorf("invalid WorkOS issuer %q", client.Issuer)
	}

	// The user's credentials and refresh token travel to this host, so only a local
	// development issuer may skip TLS.
	plainLocal := issuer.Scheme == "http" && isLoopback(issuer.Hostname())
	if issuer.Scheme != "https" && !plainLocal {
		return oauth2.Config{}, fmt.Errorf("WorkOS issuer %q must use https", client.Issuer)
	}

	host := issuer.Scheme + "://" + issuer.Host

	return oauth2.Config{
		ClientID:    client.ClientId,
		RedirectURL: callbackURL(),
		Endpoint: oauth2.Endpoint{
			AuthURL:   host + "/user_management/authorize",
			TokenURL:  host + "/user_management/authenticate",
			AuthStyle: oauth2.AuthStyleInParams,
		},
	}, nil
}

func isLoopback(hostname string) bool {
	if hostname == "localhost" {
		return true
	}

	ip := net.ParseIP(hostname)

	return ip != nil && ip.IsLoopback()
}

// NewToken converts an OAuth token into the stored form. WorkOS answers without
// expires_in, so the expiry falls back to the access token's own exp claim.
func NewToken(token *oauth2.Token, workos *config.WorkOSClient) (*config.Token, error) {
	expiresAt := token.Expiry
	if expiresAt.IsZero() {
		var err error
		expiresAt, err = accessTokenExpiry(token.AccessToken)
		if err != nil {
			return nil, err
		}
	}

	return &config.Token{
		AccessToken:  token.AccessToken,
		RefreshToken: token.RefreshToken,
		ExpiresAt:    expiresAt,
		WorkOS:       workos,
	}, nil
}

// accessTokenExpiry reads exp from a JWT without verifying it. It only schedules the
// next refresh; the API verifies the token.
func accessTokenExpiry(accessToken string) (time.Time, error) {
	parts := strings.Split(accessToken, ".")
	if len(parts) != 3 {
		return time.Time{}, errors.New("access token is not a JWT")
	}

	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return time.Time{}, fmt.Errorf("decoding access token: %w", err)
	}

	var claims struct {
		Exp int64 `json:"exp"`
	}
	if err := json.Unmarshal(payload, &claims); err != nil {
		return time.Time{}, fmt.Errorf("decoding access token: %w", err)
	}
	if claims.Exp == 0 {
		return time.Time{}, errors.New("access token has no exp claim")
	}

	return time.Unix(claims.Exp, 0), nil
}

// refreshError tells a dead session apart from a transient failure. Only an
// invalid_grant (RFC 6749 §5.2: revoked, expired, or already-rotated refresh token)
// means the user has to log in again; a timeout or a 5xx from the provider does
// not, and telling the user to re-login for those would discard a working session.
func refreshError(err error) error {
	var retrieveErr *oauth2.RetrieveError
	if errors.As(err, &retrieveErr) && retrieveErr.ErrorCode == "invalid_grant" {
		return fmt.Errorf("use 'daytona login' to reauthenticate: %w", err)
	}

	return fmt.Errorf("failed to refresh the access token, please retry: %w", err)
}

func RefreshTokenIfNeeded(ctx context.Context) error {
	c, err := config.GetConfig()
	if err != nil {
		return err
	}

	activeProfile, err := c.GetActiveProfile()
	if err != nil {
		return err
	}

	if activeProfile.Api.Key != nil {
		return nil
	}

	storedToken := activeProfile.Api.Token
	if storedToken == nil {
		return fmt.Errorf("no valid token found, use 'daytona login' to reauthenticate")
	}

	// Check if token is about to expire (within 5 minutes)
	if time.Until(storedToken.ExpiresAt) > 5*time.Minute {
		return nil
	}

	var oauth2Config oauth2.Config
	if storedToken.WorkOS != nil {
		oauth2Config, err = WorkOSConfig(*storedToken.WorkOS)
	} else {
		oauth2Config, _, err = Auth0Config(ctx)
	}
	if err != nil {
		return err
	}

	// WorkOS rotates refresh tokens, so the returned one replaces the stored one.
	newToken, err := oauth2Config.TokenSource(ctx, &oauth2.Token{RefreshToken: storedToken.RefreshToken}).Token()
	if err != nil {
		return refreshError(err)
	}

	activeProfile.Api.Token, err = NewToken(newToken, storedToken.WorkOS)
	if err != nil {
		return err
	}

	return c.EditProfile(activeProfile)
}
