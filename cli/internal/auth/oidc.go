// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

// Package auth implements the CLI's login flows:
// authorization-code + PKCE on a loopback redirect for a local browser, the
// device-authorization grant (RFC 8628) for headless hosts, refresh-token
// renewal, and the automatic project workload-key mint/cache.
package auth

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
)

// Provider is the resolved OIDC endpoint set.
type Provider struct {
	Issuer                      string `json:"issuer"`
	AuthorizationEndpoint       string `json:"authorization_endpoint"`
	TokenEndpoint               string `json:"token_endpoint"`
	DeviceAuthorizationEndpoint string `json:"device_authorization_endpoint"`
	// Not part of discovery — supplied by GET /config.
	ClientID string `json:"-"`
	Audience string `json:"-"`
	Scope    string `json:"-"`
	// AuthKit is set for a WorkOS issuer (GET /config oidc.provider
	// "workos"): its authorize endpoint refuses a request that does not name
	// the AuthKit provider.
	AuthKit bool `json:"-"`
}

// Discover fetches <issuer>/.well-known/openid-configuration.
func Discover(ctx context.Context, hc *http.Client, issuer string) (*Provider, error) {
	u := strings.TrimRight(issuer, "/") + "/.well-known/openid-configuration"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, err
	}
	resp, err := hc.Do(req)
	if err != nil {
		return nil, cerr.Wrap(cerr.ExitFailure, err, "OIDC discovery at %s failed", u)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, cerr.New(cerr.ExitFailure, "OIDC discovery at %s: HTTP %d", u, resp.StatusCode)
	}
	var p Provider
	if err := json.NewDecoder(resp.Body).Decode(&p); err != nil {
		return nil, cerr.Wrap(cerr.ExitFailure, err, "OIDC discovery: bad document")
	}
	if p.AuthorizationEndpoint == "" || p.TokenEndpoint == "" {
		return nil, cerr.New(cerr.ExitFailure, "OIDC discovery: document lacks authorization/token endpoints")
	}
	// The endpoints the document names are where the browser is sent and
	// where the refresh token is posted. Refuse anything that is not HTTPS on
	// the issuer's own host, so a tampered document cannot redirect
	// credentials elsewhere. Plain HTTP is allowed only for a loopback issuer
	// for local testing, following RFC 8252.
	issuerURL, err := url.Parse(issuer)
	if err != nil || issuerURL.Host == "" {
		return nil, cerr.New(cerr.ExitFailure, "OIDC discovery: bad issuer %q", issuer)
	}
	for name, ep := range map[string]string{"authorization_endpoint": p.AuthorizationEndpoint, "token_endpoint": p.TokenEndpoint, "device_authorization_endpoint": p.DeviceAuthorizationEndpoint} {
		if ep == "" {
			continue
		}
		u, err := url.Parse(ep)
		if err != nil || !strings.EqualFold(u.Host, issuerURL.Host) || !(u.Scheme == "https" || (u.Scheme == "http" && isLoopback(u.Hostname()))) {
			return nil, cerr.New(cerr.ExitFailure, "OIDC discovery: %s %q must be an https URL on the issuer host %s", name, ep, issuerURL.Host)
		}
	}
	return &p, nil
}

func isLoopback(host string) bool {
	if host == "localhost" {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// TokenSet is a token endpoint answer.
type TokenSet struct {
	AccessToken  string `json:"access_token"`
	RefreshToken string `json:"refresh_token"`
	IDToken      string `json:"id_token"`
	TokenType    string `json:"token_type"`
	ExpiresIn    int64  `json:"expires_in"`
	Scope        string `json:"scope"`
}

// ExpiresAt converts expires_in to an absolute time. WorkOS answers without
// expires_in, so the access token's own lifetime (exp - iat) is the next
// source: applied to our clock it is immune to skew between this machine and
// the issuer; a token without iat falls back to its absolute exp. The claims
// are read unverified because they only schedule the refresh (the API
// verifies).
func (t TokenSet) ExpiresAt(now time.Time) time.Time {
	if t.ExpiresIn > 0 {
		return now.Add(time.Duration(t.ExpiresIn) * time.Second)
	}
	if exp, iat, ok := jwtLifetime(t.AccessToken); ok {
		if iat > 0 && exp > iat {
			return now.Add(time.Duration(exp-iat) * time.Second)
		}
		return time.Unix(exp, 0)
	}
	return now.Add(time.Hour)
}

func jwtLifetime(token string) (exp, iat int64, ok bool) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return 0, 0, false
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return 0, 0, false
	}
	var claims map[string]json.RawMessage
	if json.Unmarshal(payload, &claims) != nil {
		return 0, 0, false
	}
	if json.Unmarshal(claims["exp"], &exp) != nil || exp <= 0 {
		return 0, 0, false
	}
	// A malformed iat only disables the lifetime path, never the exp.
	if json.Unmarshal(claims["iat"], &iat) != nil {
		iat = 0
	}
	return exp, iat, true
}

type tokenError struct {
	Error       string `json:"error"`
	Description string `json:"error_description"`
}

// postForm posts to the token (or device) endpoint and decodes JSON. Redirects
// are never followed: a 3xx from the token endpoint would re-post the refresh
// token (or code verifier) to whatever origin the Location header names.
func postForm(ctx context.Context, hc *http.Client, endpoint string, form url.Values, out any) (*tokenError, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, strings.NewReader(form.Encode()))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Accept", "application/json")
	resp, err := noRedirects(hc).Do(req)
	if err != nil {
		return nil, cerr.Wrap(cerr.ExitFailure, err, "POST %s failed", endpoint)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		if err := json.Unmarshal(body, out); err != nil {
			return nil, cerr.Wrap(cerr.ExitFailure, err, "POST %s: bad JSON", endpoint)
		}
		return nil, nil
	}
	var te tokenError
	if json.Unmarshal(body, &te) != nil || te.Error == "" {
		return nil, cerr.New(cerr.ExitAuth, "POST %s: HTTP %d: %s", endpoint, resp.StatusCode, strings.TrimSpace(string(body)))
	}
	return &te, nil
}

// Refresh exchanges a refresh token.
func Refresh(ctx context.Context, hc *http.Client, p *Provider, refreshToken string) (*TokenSet, error) {
	form := url.Values{
		"grant_type":    {"refresh_token"},
		"client_id":     {p.ClientID},
		"refresh_token": {refreshToken},
	}
	var ts TokenSet
	te, err := postForm(ctx, hc, p.TokenEndpoint, form, &ts)
	if err != nil {
		return nil, err
	}
	if te != nil {
		return nil, cerr.New(cerr.ExitAuth, "session expired (%s: %s)", te.Error, te.Description).
			WithHint("run `daytona login` again")
	}
	if ts.RefreshToken == "" {
		ts.RefreshToken = refreshToken // provider did not rotate it
	}
	return &ts, nil
}

// PKCE holds a code verifier/challenge pair.
type PKCE struct {
	Verifier  string
	Challenge string
	State     string
}

// NewPKCE generates verifier, S256 challenge and a random state.
func NewPKCE() (PKCE, error) {
	v, err := randomURLSafe(32)
	if err != nil {
		return PKCE{}, err
	}
	st, err := randomURLSafe(16)
	if err != nil {
		return PKCE{}, err
	}
	sum := sha256.Sum256([]byte(v))
	return PKCE{Verifier: v, Challenge: base64.RawURLEncoding.EncodeToString(sum[:]), State: st}, nil
}

func randomURLSafe(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

// AuthorizeURL builds the browser URL for the PKCE flow.
func AuthorizeURL(p *Provider, pk PKCE, redirectURI string) string {
	q := url.Values{
		"response_type":         {"code"},
		"client_id":             {p.ClientID},
		"redirect_uri":          {redirectURI},
		"scope":                 {scopeWithOffline(p.Scope)},
		"state":                 {pk.State},
		"code_challenge":        {pk.Challenge},
		"code_challenge_method": {"S256"},
	}
	if p.Audience != "" {
		q.Set("audience", p.Audience)
	}
	if p.AuthKit {
		q.Set("provider", "authkit")
	}
	return p.AuthorizationEndpoint + "?" + q.Encode()
}

// scopeWithOffline ensures offline_access so a refresh token is issued.
func scopeWithOffline(scope string) string {
	if scope == "" {
		scope = "openid profile email"
	}
	for _, s := range strings.Fields(scope) {
		if s == "offline_access" {
			return scope
		}
	}
	return scope + " offline_access"
}

// ExchangeCode redeems an authorization code.
func ExchangeCode(ctx context.Context, hc *http.Client, p *Provider, pk PKCE, code, redirectURI string) (*TokenSet, error) {
	form := url.Values{
		"grant_type":    {"authorization_code"},
		"client_id":     {p.ClientID},
		"code":          {code},
		"redirect_uri":  {redirectURI},
		"code_verifier": {pk.Verifier},
	}
	var ts TokenSet
	te, err := postForm(ctx, hc, p.TokenEndpoint, form, &ts)
	if err != nil {
		return nil, err
	}
	if te != nil {
		return nil, cerr.New(cerr.ExitAuth, "login failed (%s: %s)", te.Error, te.Description)
	}
	return &ts, nil
}

// DeviceCode is a device-authorization response (RFC 8628 section 3.2).
type DeviceCode struct {
	DeviceCode              string `json:"device_code"`
	UserCode                string `json:"user_code"`
	VerificationURI         string `json:"verification_uri"`
	VerificationURIComplete string `json:"verification_uri_complete"`
	ExpiresIn               int64  `json:"expires_in"`
	Interval                int64  `json:"interval"`
}

// StartDevice requests a device code.
func StartDevice(ctx context.Context, hc *http.Client, p *Provider) (*DeviceCode, error) {
	if p.DeviceAuthorizationEndpoint == "" {
		return nil, cerr.New(cerr.ExitUsage, "this identity provider does not advertise a device authorization endpoint").
			WithHint("log in on a machine with a browser, or use a client ID with the device grant enabled")
	}
	form := url.Values{"client_id": {p.ClientID}, "scope": {scopeWithOffline(p.Scope)}}
	if p.Audience != "" {
		form.Set("audience", p.Audience)
	}
	var dc DeviceCode
	te, err := postForm(ctx, hc, p.DeviceAuthorizationEndpoint, form, &dc)
	if err != nil {
		return nil, err
	}
	if te != nil {
		return nil, cerr.New(cerr.ExitAuth, "device authorization refused (%s: %s)", te.Error, te.Description).
			WithHint("the CLI client id may lack the Device Authorization grant")
	}
	if dc.Interval <= 0 {
		dc.Interval = 5
	}
	return &dc, nil
}

// ErrDevicePending is returned by PollDevice while the user has not approved.
var ErrDevicePending = errors.New("authorization_pending")

// PollDevice polls the token endpoint until the device code is approved,
// denied or expired.
func PollDevice(ctx context.Context, hc *http.Client, p *Provider, dc *DeviceCode, sleep func(time.Duration)) (*TokenSet, error) {
	interval := time.Duration(dc.Interval) * time.Second
	deadline := time.Now().Add(time.Duration(dc.ExpiresIn) * time.Second)
	for {
		if ctx.Err() != nil {
			return nil, cerr.New(cerr.ExitInterrupted, "interrupted")
		}
		form := url.Values{
			"grant_type":  {"urn:ietf:params:oauth:grant-type:device_code"},
			"client_id":   {p.ClientID},
			"device_code": {dc.DeviceCode},
		}
		var ts TokenSet
		te, err := postForm(ctx, hc, p.TokenEndpoint, form, &ts)
		if err != nil {
			return nil, err
		}
		if te == nil {
			return &ts, nil
		}
		switch te.Error {
		case "authorization_pending":
		case "slow_down":
			interval += 5 * time.Second
		case "expired_token":
			return nil, cerr.New(cerr.ExitAuth, "the device code expired before it was approved").WithHint("run `daytona login --device` again")
		case "access_denied":
			return nil, cerr.New(cerr.ExitAuth, "login was denied")
		default:
			return nil, cerr.New(cerr.ExitAuth, "device login failed (%s: %s)", te.Error, te.Description)
		}
		if time.Now().After(deadline) {
			return nil, cerr.New(cerr.ExitAuth, "the device code expired before it was approved").WithHint("run `daytona login --device` again")
		}
		if sleep != nil {
			sleep(interval) // test hook: records the interval, returns at once
			continue
		}
		// Context-aware wait: Ctrl-C must not block for the whole interval.
		select {
		case <-ctx.Done():
			return nil, cerr.New(cerr.ExitInterrupted, "interrupted")
		case <-time.After(interval):
		}
	}
}

// FormatDevicePrompt renders the instruction shown to the user.
func FormatDevicePrompt(dc *DeviceCode) string {
	if dc.VerificationURIComplete != "" {
		return fmt.Sprintf("Open %s\nand confirm the code %s", dc.VerificationURIComplete, dc.UserCode)
	}
	return fmt.Sprintf("Open %s\nand enter the code %s", dc.VerificationURI, dc.UserCode)
}

// noRedirects returns a shallow copy of hc that refuses to follow redirects.
func noRedirects(hc *http.Client) *http.Client {
	cp := *hc
	cp.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	return &cp
}
