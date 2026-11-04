// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
)

func TestPKCEAndAuthorizeURL(t *testing.T) {
	pk, err := NewPKCE()
	if err != nil || len(pk.Verifier) < 43 || pk.Challenge == "" || pk.State == "" {
		t.Fatal(err, pk)
	}
	p := &Provider{AuthorizationEndpoint: "https://idp/authorize", ClientID: "cid", Audience: "aud", Scope: "openid"}
	u, _ := url.Parse(AuthorizeURL(p, pk, "http://127.0.0.1:1/callback"))
	q := u.Query()
	if q.Get("code_challenge_method") != "S256" || q.Get("code_challenge") != pk.Challenge || q.Get("audience") != "aud" {
		t.Fatal(q)
	}
	if !strings.Contains(q.Get("scope"), "offline_access") {
		t.Fatal("offline_access must be requested for a refresh token")
	}
	if q.Has("provider") {
		t.Fatalf("a generic OIDC issuer must not get the WorkOS provider selector: %v", q)
	}
	p.AuthKit = true
	u, _ = url.Parse(AuthorizeURL(p, pk, "http://127.0.0.1:1/callback"))
	if got := u.Query().Get("provider"); got != "authkit" {
		t.Fatalf("WorkOS refuses an authorize request without provider=authkit; got %q", got)
	}
}

func TestTokenSetExpiresAtFallsBackToAccessTokenExp(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	if got := (TokenSet{ExpiresIn: 60}).ExpiresAt(now); !got.Equal(now.Add(time.Minute)) {
		t.Fatalf("expires_in wins: %v", got)
	}
	jwt := "h." + base64.RawURLEncoding.EncodeToString([]byte(`{"exp":1000300}`)) + ".s"
	if got := (TokenSet{AccessToken: jwt}).ExpiresAt(now); !got.Equal(time.Unix(1_000_300, 0)) {
		t.Fatalf("without expires_in or iat the access token's absolute exp schedules the refresh: %v", got)
	}
	// Issuer clock 10 minutes ahead of ours: exp - iat (300 s) applied to our
	// clock, never the absolute exp, which would read as 15 minutes away.
	skewed := "h." + base64.RawURLEncoding.EncodeToString([]byte(`{"iat":1000600,"exp":1000900}`)) + ".s"
	if got := (TokenSet{AccessToken: skewed}).ExpiresAt(now); !got.Equal(now.Add(300 * time.Second)) {
		t.Fatalf("with iat the token's lifetime is applied to the local clock: %v", got)
	}
	inverted := "h." + base64.RawURLEncoding.EncodeToString([]byte(`{"iat":1000900,"exp":1000300}`)) + ".s"
	if got := (TokenSet{AccessToken: inverted}).ExpiresAt(now); !got.Equal(time.Unix(1_000_300, 0)) {
		t.Fatalf("an iat at or after exp is ignored: %v", got)
	}
	for _, badIat := range []string{`1000000.5`, `"x"`, `null`} {
		tok := "h." + base64.RawURLEncoding.EncodeToString([]byte(`{"iat":`+badIat+`,"exp":1000300}`)) + ".s"
		if got := (TokenSet{AccessToken: tok}).ExpiresAt(now); !got.Equal(time.Unix(1_000_300, 0)) {
			t.Fatalf("iat %s: a malformed iat falls back to the absolute exp, not to one hour: %v", badIat, got)
		}
	}
	for _, opaque := range []string{"opaque", "h.!!.s", "h." + base64.RawURLEncoding.EncodeToString([]byte(`{}`)) + ".s", "h." + base64.RawURLEncoding.EncodeToString([]byte(`[1]`)) + ".s"} {
		if got := (TokenSet{AccessToken: opaque}).ExpiresAt(now); !got.Equal(now.Add(time.Hour)) {
			t.Fatalf("%q: no usable exp falls back to one hour: %v", opaque, got)
		}
	}
}

func idp(t *testing.T, handler func(w http.ResponseWriter, r *http.Request)) (*httptest.Server, *Provider) {
	srv := httptest.NewServer(http.HandlerFunc(handler))
	t.Cleanup(srv.Close)
	return srv, &Provider{TokenEndpoint: srv.URL + "/token", DeviceAuthorizationEndpoint: srv.URL + "/device", ClientID: "cid"}
}

func TestDiscover(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/.well-known/openid-configuration" {
			w.WriteHeader(404)
			return
		}
		base := "https://" + r.Host
		_ = json.NewEncoder(w).Encode(map[string]string{"issuer": base, "authorization_endpoint": base + "/a", "token_endpoint": base + "/t", "device_authorization_endpoint": base + "/d"})
	}))
	defer srv.Close()
	p, err := Discover(context.Background(), srv.Client(), srv.URL+"/")
	if err != nil || !strings.HasSuffix(p.DeviceAuthorizationEndpoint, "/d") {
		t.Fatal(err, p)
	}
}

func TestExchangeAndRefresh(t *testing.T) {
	var lastForm url.Values
	srv, p := idp(t, func(w http.ResponseWriter, r *http.Request) {
		_ = r.ParseForm()
		lastForm = r.PostForm
		if r.PostForm.Get("grant_type") == "refresh_token" && r.PostForm.Get("refresh_token") == "dead" {
			w.WriteHeader(403)
			_, _ = w.Write([]byte(`{"error":"invalid_grant","error_description":"expired"}`))
			return
		}
		_ = json.NewEncoder(w).Encode(TokenSet{AccessToken: "at", RefreshToken: "", ExpiresIn: 100})
	})
	_ = srv
	pk, _ := NewPKCE()
	ts, err := ExchangeCode(context.Background(), srv.Client(), p, pk, "code1", "http://127.0.0.1:9/callback")
	if err != nil || ts.AccessToken != "at" || lastForm.Get("code_verifier") != pk.Verifier {
		t.Fatal(err, lastForm)
	}
	rs, err := Refresh(context.Background(), srv.Client(), p, "rt")
	if err != nil || rs.RefreshToken != "rt" {
		t.Fatalf("refresh should keep old refresh token when not rotated: %v %+v", err, rs)
	}
	_, err = Refresh(context.Background(), srv.Client(), p, "dead")
	if cerr.ExitCode(err) != cerr.ExitAuth {
		t.Fatal(err)
	}
	if ts.ExpiresAt(time.Unix(0, 0)).Unix() != 100 {
		t.Fatal()
	}
}

func TestDeviceFlow(t *testing.T) {
	polls := 0
	srv, p := idp(t, func(w http.ResponseWriter, r *http.Request) {
		_ = r.ParseForm()
		switch r.URL.Path {
		case "/device":
			_ = json.NewEncoder(w).Encode(DeviceCode{DeviceCode: "dc", UserCode: "ABCD-1234", VerificationURI: "https://idp/activate", ExpiresIn: 600, Interval: 1})
		case "/token":
			polls++
			if r.PostForm.Get("device_code") != "dc" {
				t.Errorf("device_code %q", r.PostForm.Get("device_code"))
			}
			switch polls {
			case 1:
				w.WriteHeader(403)
				_, _ = w.Write([]byte(`{"error":"authorization_pending"}`))
			case 2:
				w.WriteHeader(403)
				_, _ = w.Write([]byte(`{"error":"slow_down"}`))
			default:
				_ = json.NewEncoder(w).Encode(TokenSet{AccessToken: "at", RefreshToken: "rt", ExpiresIn: 10})
			}
		}
	})
	dc, err := StartDevice(context.Background(), srv.Client(), p)
	if err != nil || dc.UserCode != "ABCD-1234" {
		t.Fatal(err)
	}
	if !strings.Contains(FormatDevicePrompt(dc), "ABCD-1234") {
		t.Fatal()
	}
	var slept []time.Duration
	ts, err := PollDevice(context.Background(), srv.Client(), p, dc, func(d time.Duration) { slept = append(slept, d) })
	if err != nil || ts.AccessToken != "at" || polls != 3 {
		t.Fatal(err, polls)
	}
	if len(slept) != 2 || slept[1] != 6*time.Second {
		t.Fatalf("slow_down must add 5s: %v", slept)
	}
	// denied
	polls = 0
	srv2, p2 := idp(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/token" {
			w.WriteHeader(403)
			_, _ = w.Write([]byte(`{"error":"access_denied"}`))
		}
	})
	_ = srv2
	_, err = PollDevice(context.Background(), srv2.Client(), p2, &DeviceCode{DeviceCode: "x", ExpiresIn: 60, Interval: 1}, func(time.Duration) {})
	if cerr.ExitCode(err) != cerr.ExitAuth {
		t.Fatal(err)
	}
	// no device endpoint
	_, err = StartDevice(context.Background(), srv.Client(), &Provider{ClientID: "c"})
	if cerr.ExitCode(err) != cerr.ExitUsage {
		t.Fatal(err)
	}
}

func TestLoopback(t *testing.T) {
	lb, err := ListenLoopback("s1")
	if err != nil {
		t.Fatal(err)
	}
	uri := lb.RedirectURI()
	go func() {
		resp, err := http.Get(uri + "?code=c1&state=s1")
		if err == nil {
			resp.Body.Close()
		}
	}()
	r, err := lb.Wait(context.Background(), 5*time.Second)
	if err != nil || r.Code != "c1" || r.State != "s1" {
		t.Fatal(err, r)
	}
	lb2, _ := ListenLoopback("s2")
	_, err = lb2.Wait(context.Background(), 10*time.Millisecond)
	if cerr.ExitCode(err) != cerr.ExitTimeout {
		t.Fatal(err)
	}
}

func TestLoopback_stateMismatchReturns400AndKeepsWaiting(t *testing.T) {
	lb, err := ListenLoopback("expected")
	if err != nil {
		t.Fatal(err)
	}
	defer lb.Close()

	wrong, err := http.Get(lb.RedirectURI() + "?code=c1&state=wrong")
	if err != nil {
		t.Fatal(err)
	}
	wrong.Body.Close()
	if wrong.StatusCode != http.StatusBadRequest {
		t.Fatalf("status %d", wrong.StatusCode)
	}

	go func() {
		resp, reqErr := http.Get(lb.RedirectURI() + "?code=c1&state=expected")
		if reqErr == nil {
			resp.Body.Close()
		}
	}()

	result, err := lb.Wait(context.Background(), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if result.State != "expected" || result.Code != "c1" {
		t.Fatalf("result=%+v", result)
	}
}

func TestLoopbackErrorPageEscapesProviderText(t *testing.T) {
	lb, err := ListenLoopback("state")
	if err != nil {
		t.Fatal(err)
	}
	defer lb.Close()
	evil := `<script>alert(1)</script>"onload="x`
	resp, err := http.Get(lb.RedirectURI() + "?error=access_denied&state=state&error_description=" + url.QueryEscape(evil))
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status %d", resp.StatusCode)
	}
	if strings.Contains(string(body), "<script>") || strings.Contains(string(body), `"onload=`) {
		t.Fatalf("provider text reached the page unescaped: %s", body)
	}
	if !strings.Contains(string(body), "&lt;script&gt;") {
		t.Fatalf("expected escaped text, got: %s", body)
	}
	if r := <-lb.result; !strings.Contains(r.Err, "access_denied") {
		t.Fatal(r)
	}
}

func TestDiscoverRefusesForeignOrPlainEndpoints(t *testing.T) {
	for name, doc := range map[string]map[string]string{
		"foreign host":   {"issuer": "x", "authorization_endpoint": "https://IDP/a", "token_endpoint": "https://evil.example/t"},
		"foreign device": {"issuer": "x", "authorization_endpoint": "https://IDP/a", "token_endpoint": "https://IDP/t", "device_authorization_endpoint": "https://evil.example/d"},
	} {
		t.Run(name, func(t *testing.T) {
			srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				host := r.Host
				if name == "plain http on a public host" {
					host = "idp.example" // http is tolerated only on loopback
				}
				out := map[string]string{}
				for k, v := range doc {
					out[k] = strings.ReplaceAll(v, "IDP", host)
				}
				_ = json.NewEncoder(w).Encode(out)
			}))
			defer srv.Close()
			_, err := Discover(context.Background(), srv.Client(), srv.URL)
			if err == nil {
				t.Fatal("tampered discovery document accepted")
			}
		})
	}
}

func TestTokenPostDoesNotFollowRedirects(t *testing.T) {
	var leaked int32
	evil := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&leaked, 1)
		w.WriteHeader(200)
	}))
	defer evil.Close()
	idp := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, evil.URL+"/steal", http.StatusTemporaryRedirect)
	}))
	defer idp.Close()
	p := &Provider{TokenEndpoint: idp.URL + "/token", ClientID: "c"}
	_, err := Refresh(context.Background(), idp.Client(), p, "rt-secret")
	if err == nil {
		t.Fatal("a redirected token endpoint must fail")
	}
	if atomic.LoadInt32(&leaked) != 0 {
		t.Fatal("refresh token was re-posted to the redirect target")
	}
}

// An error redirect WITHOUT state (an RFC 6749 section 4.1.2.1 violation) is refused,
// not delivered — but the browser page names the provider's error and the
// terminal gets a notice instead of silence until the timeout.
func TestLoopback_stateLessErrorRedirectIsRefusedLoudly(t *testing.T) {
	lb, err := ListenLoopback("expected")
	if err != nil {
		t.Fatal(err)
	}
	defer lb.Close()
	var notices []string
	lb.Notice = func(line string) { notices = append(notices, line) }

	resp, err := http.Get(lb.RedirectURI() + "?error=access_denied&error_description=user+said+no")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status %d", resp.StatusCode)
	}
	page := string(body)
	for _, want := range []string{"access_denied", "user said no", "cannot be verified", "Return to the terminal"} {
		if !strings.Contains(page, want) {
			t.Fatalf("page lacks %q:\n%s", want, page)
		}
	}
	if len(notices) != 1 || !strings.Contains(notices[0], "access_denied") || !strings.Contains(notices[0], "still waiting") {
		t.Fatalf("terminal notice: %v", notices)
	}
	// Not delivered: Wait still times out rather than reporting a result.
	if _, err := lb.Wait(context.Background(), 200*time.Millisecond); err == nil {
		t.Fatal("a state-less error redirect must not be delivered as a result")
	}
}
