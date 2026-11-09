// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/auth"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/stretchr/testify/require"
)

const (
	testUserID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
	testOrgP   = "11111111-1111-1111-1111-111111111111"
	testOrgA   = "22222222-2222-2222-2222-222222222222"
	testOrgB   = "33333333-3333-3333-3333-333333333333"
	testProjP1 = "44444444-4444-4444-4444-444444444444"
	testProjA1 = "55555555-5555-5555-5555-555555555555"
	testProjA2 = "66666666-6666-6666-6666-666666666666"
	testProjP2 = "77777777-7777-7777-7777-777777777777"
)

func TestLoginTokenAndWhoami(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	out, errb, code := run(t, d, nil, "login", "--token", "dtn_abc", "--api-url", f.srv.URL)
	if code != 0 {
		t.Fatalf("login: %d %s", code, errb)
	}
	if out != "" {
		t.Fatalf("login must not write to stdout: %q", out)
	}
	out, _, code = run(t, d, nil, "whoami", "-o", "json")
	if code != 0 {
		t.Fatal(code)
	}
	m := mustJSON(t, out)
	if m["credential_source"] != "profile-token" || m["api_url"] != f.srv.URL {
		t.Fatalf("%v", m)
	}
	// Explicit --api-key wins over the saved profile token and is reported.
	out, _, _ = run(t, d, nil, "whoami", "-o", "json", "--api-key", "other")
	if mustJSON(t, out)["credential_source"] != "flag" {
		t.Fatal(out)
	}
	// Logout is local only: no HTTP call, credentials gone.
	n := len(f.requests)
	_, _, code = run(t, d, nil, "logout")
	if code != 0 || len(f.requests) != n {
		t.Fatalf("logout must not call the server (%d requests)", len(f.requests)-n)
	}
	_, errb, code = run(t, d, nil, "org", "list")
	if code != cerr.ExitAuth || !has(errb, "daytona login") {
		t.Fatalf("after logout: %d %s", code, errb)
	}
}

// oidcProfile seeds a logged-in OIDC profile directly (the browser flow is
// exercised in internal/auth).
func oidcProfile(t *testing.T, d, apiURL string, exp time.Duration) {
	st, _ := config.Open(d)
	err := st.Update(func(c *config.Config, cr *config.Credentials) (bool, bool, error) {
		c.Upsert(config.Profile{Name: "default", APIURL: apiURL, Credential: "oidc", Identity: testUserID, OrgID: testOrgP, ProjectByOrg: map[string]string{testOrgP: testProjP1}})
		c.ActiveProfile = "default"
		cr.Profiles["default"] = config.ProfileCredentials{OIDC: &config.Tokens{AccessToken: "jwt", RefreshToken: "rt", ExpiresAt: time.Now().Add(exp)}}
		return true, true, nil
	})
	if err != nil {
		t.Fatal(err)
	}
}

func TestOrgUseAmbiguousAndRemembered(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgA+"/projects", 200, map[string]any{"projects": []map[string]any{{"id": testProjA1, "org_id": testOrgA, "name": "default", "default": true, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"}, {"id": testProjA2, "org_id": testOrgA, "name": "experiments", "default": false, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"}}})
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)

	_, errb, code := run(t, d, nil, "org", "use", "ACME")
	if code != cerr.ExitUsage || !has(errb, "ambiguous") {
		t.Fatalf("ambiguous name must fail: %d %s", code, errb)
	}
	_, errb, code = run(t, d, nil, "org", "use", testOrgA)
	if code != 0 || !has(errb, testProjA1) {
		t.Fatalf("org use by id → default project: %d %s", code, errb)
	}
	_, _, code = run(t, d, nil, "project", "use", "experiments")
	if code != 0 {
		t.Fatal(code)
	}
	// Switch away and back: the remembered project is restored.
	_, _, _ = run(t, d, nil, "org", "use", testOrgP)
	_, errb, _ = run(t, d, nil, "org", "use", "Acme")
	if !has(errb, testProjA2) {
		t.Fatalf("remembered project not restored: %s", errb)
	}
	// Remembered project vanished → actionable error, no silent retarget.
	f.on("GET", "/orgs/"+testOrgA+"/projects", 200, map[string]any{"projects": []map[string]any{{"id": testProjA1, "org_id": testOrgA, "name": "default", "default": true, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"}}})
	_, errb, code = run(t, d, nil, "org", "use", testOrgA)
	if code != cerr.ExitNotFound || !has(errb, "project use") {
		t.Fatalf("%d %s", code, errb)
	}
	out, _, _ := run(t, d, nil, "org", "list", "-o", "json")
	m := mustJSON(t, out)
	if len(m["items"].([]any)) != 3 {
		t.Fatal(out)
	}
	// Bearer sent as-is.
	if f.requests[len(f.requests)-1].Auth != "Bearer jwt" {
		t.Fatal(f.requests[len(f.requests)-1].Auth)
	}
}

func TestOrgUseWithoutDefaultLeavesProjectUnset(t *testing.T) {
	f := newFakeAPI(t)
	orgs := organizationsFixture()
	orgs["organizations"].([]map[string]any)[1]["membership"].(map[string]any)["default_project_id"] = "00000000-0000-0000-0000-000000000000"
	f.on("GET", "/orgs", 200, orgs)
	f.on("GET", "/orgs/"+testOrgA+"/projects", 200, map[string]any{"projects": []any{}})
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)

	_, errb, code := run(t, d, nil, "org", "use", testOrgA)
	if code != 0 {
		t.Fatalf("org use without a default project: %d %s", code, errb)
	}
	st, _ := config.Open(d)
	cfg, err := st.LoadConfig()
	if err != nil {
		t.Fatal(err)
	}
	profile, ok := cfg.Profile("default")
	if !ok {
		t.Fatal("default profile missing")
	}
	if project := profile.ProjectByOrg[testOrgA]; project != "" {
		t.Fatalf("nil UUID must not be remembered as a project: %q", project)
	}
}

func TestProjectKeyMintAndCache(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/orgs", 200, organizationsFixture())
	mints := 0
	f.handle("POST", "/projects/"+testProjP1+"/api-keys", func(w http.ResponseWriter, r *http.Request) {
		mints++
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(apiKeyFixture("8", testProjP1, "dtn_minted", []string{"write:sandboxes", "read:sandboxes"})))
	})
	f.on("GET", "/regions", 200, map[string]any{"regions": []any{}, "default_region_id": nil})
	f.on("GET", "/sandboxes", 200, map[string]any{"sandboxes": []any{}, "next_cursor": nil})
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)

	_, errb, code := run(t, d, nil, "sandbox", "list", "-o", "json")
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	// Warm path: the cached key is used without another organization lookup.
	warmFrom := len(f.requests)
	_, _, _ = run(t, d, nil, "sandbox", "list", "-o", "json")
	if mints != 1 {
		t.Fatalf("expected one mint, got %d", mints)
	}
	for _, r := range f.requests[warmFrom:] {
		if r.Path == "/orgs" {
			t.Fatal("a warm project-key cache must not call GET /orgs")
		}
	}
	// The mint body carries the ceiling, a non-default name and an expiry.
	for _, r := range f.requests {
		if r.Method == "POST" && r.Path == "/projects/"+testProjP1+"/api-keys" {
			if r.Body["name"] == "default" || r.Body["name"] == nil || r.Body["expires_at"] == nil {
				t.Fatalf("mint body: %v", r.Body)
			}
			perms := r.Body["project_permissions"].([]any)
			if len(perms) != 2 {
				t.Fatalf("ceiling: %v", perms)
			}
		}
		if r.Path == "/sandboxes" && r.Auth != "Bearer dtn_minted" {
			t.Fatalf("sandbox route must use the minted key, got %q", r.Auth)
		}
	}
	// Ceiling widened. Widening never fails a call the old key already
	// allowed, so nothing is re-minted for a list — but a call the WIDER
	// ceiling newly allows answers 403 under the old key, which drops it and
	// re-mints under the current ceiling, then retries once.
	wider := organizationsFixture()
	orgs := wider["organizations"].([]map[string]any)
	first := make(map[string]any, len(orgs[0]))
	for k, v := range orgs[0] {
		first[k] = v
	}
	membership := first["membership"].(map[string]any)
	perms := append([]string{}, membership["grantable_project_permissions"].([]string)...)
	membership["grantable_project_permissions"] = append(perms, "delete:sandboxes")
	orgs[0] = first
	wider["organizations"] = orgs
	f.on("GET", "/orgs", 200, wider)
	f.handle("POST", "/projects/"+testProjP1+"/api-keys", func(w http.ResponseWriter, r *http.Request) {
		mints++
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(apiKeyFixture("9", testProjP1, "dtn_minted_wide", []string{"write:sandboxes", "read:sandboxes", "delete:sandboxes"})))
	})
	f.handle("GET", "/sandboxes/7fJk2mQpXv9dLc3RtY", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(sandboxDetailFixture("7fJk2mQpXv9dLc3RtY", "linux", "running")))
	})
	f.handle("DELETE", "/sandboxes/7fJk2mQpXv9dLc3RtY", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer dtn_minted_wide" {
			w.WriteHeader(403)
			_, _ = w.Write([]byte(`{"code":"FORBIDDEN","message":"permission denied","source":"DAYTONA_API"}`))
			return
		}
		w.WriteHeader(204)
	})
	_, _, _ = run(t, d, nil, "sandbox", "list", "-o", "json")
	if mints != 1 {
		t.Fatalf("widening alone must not re-mint while the key still works, got %d mints", mints)
	}
	_, errb, code = run(t, d, nil, "sandbox", "delete", "7fJk2mQpXv9dLc3RtY", "--yes", "--no-wait", "-o", "json")
	if code != 0 {
		t.Fatalf("403 under the narrow key must re-mint under the wider ceiling and retry: %d %s", code, errb)
	}
	if mints != 2 {
		t.Fatalf("expected exactly one re-mint after the 403, got %d", mints)
	}
	// Explicit key: no /orgs, no mint, bearer as-is.
	before := len(f.requests)
	_, _, code = run(t, d, nil, "sandbox", "list", "-o", "json", "--api-key", "dtn_static")
	if code != 0 {
		t.Fatal(code)
	}
	for _, r := range f.requests[before:] {
		if r.Path == "/orgs" || r.Method == "POST" {
			t.Fatalf("explicit key must not trigger an organization lookup: %+v", r)
		}
		if r.Path == "/sandboxes" && r.Auth != "Bearer dtn_static" {
			t.Fatal(r.Auth)
		}
	}
}

func TestExpiredSessionRefreshes(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
	f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{"issuer": f.srv.URL + "/idp", "authorization_endpoint": f.srv.URL + "/idp/authorize", "token_endpoint": f.srv.URL + "/idp/token"})
	f.handle("POST", "/idp/token", func(w http.ResponseWriter, r *http.Request) {
		_ = r.ParseForm()
		if r.PostForm.Get("client_id") != "native" || r.PostForm.Get("grant_type") != "refresh_token" {
			w.WriteHeader(400)
			_, _ = w.Write([]byte(`{"error":"invalid_request","error_description":"` + r.PostForm.Encode() + `"}`))
			return
		}
		_, _ = w.Write([]byte(`{"access_token":"jwt2","refresh_token":"rt2","expires_in":3600}`))
	})
	f.on("GET", "/orgs", 200, organizationsFixture())
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, -time.Minute) // expired: must refresh before /orgs
	_, errb, code := run(t, d, nil, "org", "list", "-o", "json")
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	last := f.requests[len(f.requests)-1]
	if last.Path != "/orgs" || last.Auth != "Bearer jwt2" {
		t.Fatalf("refreshed token not used: %+v", last)
	}
	st, _ := config.Open(d)
	cr, _ := st.LoadCredentials()
	if cr.Profiles["default"].OIDC.RefreshToken != "rt2" || cr.Profiles["default"].OIDC.AccessToken != "jwt2" {
		t.Fatalf("rotated tokens not persisted: %+v", cr.Profiles["default"].OIDC)
	}
}

// The refresh's network calls run OUTSIDE the config lock (a slow issuer must
// not block other invocations), and a concurrent invocation that rotated the
// tokens meanwhile wins: our refresh (with the now-consumed refresh token)
// fails with invalid_grant, but the stored fresh tokens are used instead.
func TestTokenRefreshDoesNotHoldLockAndConvergesOnRotation(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
	f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{"issuer": f.srv.URL + "/idp", "authorization_endpoint": f.srv.URL + "/idp/authorize", "token_endpoint": f.srv.URL + "/idp/token"})
	f.handle("POST", "/idp/token", func(w http.ResponseWriter, r *http.Request) {
		// While the refresh is in flight, another invocation rotates the
		// stored tokens. It can do so only if the lock is NOT held here.
		st, _ := config.Open(d)
		done := make(chan error, 1)
		go func() {
			done <- st.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
				pc := cr.Profiles["default"]
				pc.OIDC = &config.Tokens{AccessToken: "jwt-other", RefreshToken: "rt-other", ExpiresAt: time.Now().Add(time.Hour)}
				cr.Profiles["default"] = pc
				return false, true, nil
			})
		}()
		select {
		case err := <-done:
			if err != nil {
				t.Errorf("concurrent update: %v", err)
			}
		case <-time.After(5 * time.Second):
			t.Error("the store lock is held across the refresh network call")
		}
		// The snapshotted refresh token has been consumed by "the other".
		w.WriteHeader(400)
		_, _ = w.Write([]byte(`{"error":"invalid_grant","error_description":"refresh token rotated"}`))
	})
	f.on("GET", "/orgs", 200, organizationsFixture())
	oidcProfile(t, d, f.srv.URL, -time.Minute)
	_, errb, code := run(t, d, nil, "org", "list", "-o", "json")
	if code != 0 {
		t.Fatalf("must converge on the concurrently refreshed token: %d %s", code, errb)
	}
	last := f.requests[len(f.requests)-1]
	if last.Path != "/orgs" || last.Auth != "Bearer jwt-other" {
		t.Fatalf("expected the other invocation's token on /orgs: %+v", last)
	}
}

// The other invocation may redeem the refresh token first but save its
// tokens only after our refresh was rejected: the rejected refresh rechecks
// the store briefly instead of reporting an expired session.
func TestTokenRefreshRejectedBeforeConcurrentRotationIsSaved(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
	f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{"issuer": f.srv.URL + "/idp", "authorization_endpoint": f.srv.URL + "/idp/authorize", "token_endpoint": f.srv.URL + "/idp/token"})
	saved := make(chan error, 1)
	f.handle("POST", "/idp/token", func(w http.ResponseWriter, r *http.Request) {
		go func() {
			time.Sleep(200 * time.Millisecond)
			st, err := config.Open(d)
			if err != nil {
				saved <- err
				return
			}
			saved <- st.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
				pc := cr.Profiles["default"]
				pc.OIDC = &config.Tokens{AccessToken: "jwt-other", RefreshToken: "rt-other", ExpiresAt: time.Now().Add(time.Hour)}
				cr.Profiles["default"] = pc
				return false, true, nil
			})
		}()
		w.WriteHeader(400)
		_, _ = w.Write([]byte(`{"error":"invalid_grant","error_description":"refresh token rotated"}`))
	})
	f.on("GET", "/orgs", 200, organizationsFixture())
	oidcProfile(t, d, f.srv.URL, -time.Minute)
	_, errb, code := run(t, d, nil, "org", "list", "-o", "json")
	select {
	case err := <-saved:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatalf("the token endpoint was never called: %d %s", code, errb)
	}
	if code != 0 {
		t.Fatalf("must converge on the tokens saved after the rejection: %d %s", code, errb)
	}
	last := f.requests[len(f.requests)-1]
	if last.Path != "/orgs" || last.Auth != "Bearer jwt-other" {
		t.Fatalf("expected the other invocation's token on /orgs: %+v", last)
	}
}

// A rejected refresh that no other invocation rotated still reports the
// expired session, after the bounded recheck.
func TestTokenRefreshRejectedWithoutRotationExpiresTheSession(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
	f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{"issuer": f.srv.URL + "/idp", "authorization_endpoint": f.srv.URL + "/idp/authorize", "token_endpoint": f.srv.URL + "/idp/token"})
	f.handle("POST", "/idp/token", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(400)
		_, _ = w.Write([]byte(`{"error":"invalid_grant","error_description":"expired"}`))
	})
	oidcProfile(t, d, f.srv.URL, -time.Minute)
	start := time.Now()
	_, errb, code := run(t, d, nil, "org", "list", "-o", "json")
	if code != cerr.ExitAuth || !has(errb, "session expired") {
		t.Fatalf("%d %s", code, errb)
	}
	if elapsed := time.Since(start); elapsed > 5*time.Second {
		t.Fatalf("the recheck is not bounded: %s", elapsed)
	}
}

func TestDestructiveNeedsYesOffTTY(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	_, _, _ = run(t, d, nil, "login", "--token", "k", "--api-url", f.srv.URL)
	_, errb, code := run(t, d, nil, "profile", "delete", "default")
	if code != cerr.ExitUsage || !has(errb, "--yes") {
		t.Fatalf("%d %s", code, errb)
	}
	_, _, code = run(t, d, nil, "profile", "delete", "default", "--yes")
	if code != 0 {
		t.Fatal(code)
	}
	_, _, _ = run(t, d, nil, "login", "--token", "k", "--api-url", f.srv.URL)
	_, _, code = run(t, d, map[string]string{"DAYTONA_YES": "1"}, "profile", "delete", "default")
	if code != 0 {
		t.Fatal("DAYTONA_YES must skip the prompt")
	}
}

// A login-sourced profile uses the minted project key for sandbox and
// toolbox requests, never the account JWT.
func TestToolboxUsesProjectKeyUnderLogin(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("POST", "/projects/"+testProjP1+"/api-keys", 201, apiKeyFixture("8", testProjP1, "dtn_minted", []string{"write:sandboxes", "read:sandboxes"}))
	f.handle("GET", "/sandboxes/sbx", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"id":"sbx","class":"linux","name":"box","status":"running","region_id":"r1","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"spot":false,"port_map":[],"disk_mib":1024,"memory_mib":512,"cpu":1,"cores_max":1,"toolbox_proxy_url":"` + f.srv.URL + `/test-tools"}`))
	})
	f.on("GET", "/test-tools/sbx/port", 200, `{"ports":[8080]}`)
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	_, errb, code := run(t, d, nil, "sandbox", "port", "list", "sbx", "-o", "json")
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	seen := 0
	for _, r := range f.requests {
		if r.Path == "/sandboxes/sbx" || strings.HasPrefix(r.Path, "/test-tools/") {
			seen++
			if r.Auth != "Bearer dtn_minted" {
				t.Fatalf("%s %s sent %q, want the minted key", r.Method, r.Path, r.Auth)
			}
		}
	}
	if seen < 2 {
		t.Fatalf("expected /sandboxes + toolbox calls, saw %d", seen)
	}
}

// Off-TTY, a destructive verb without --yes must refuse with exit 2
// BEFORE any request — even when the API is unreachable.
func TestDestructiveRefusalPrecedesNetwork(t *testing.T) {
	d := cfgDir(t)
	dead := "http://127.0.0.1:1"
	cases := [][]string{
		{"sandbox", "delete", "s-1"},
		{"org", "delete", "o-1"},
		{"org", "leave", "o-1"},
		{"project", "delete", "p-1"},
		{"member", "remove", "u-1"},
		{"invite", "revoke", "inv-1"},
		{"project", "member", "remove", "u-1"},
		{"project", "service-account", "remove", "sa-1"},
		{"key", "revoke", "k-1"},
		{"profile", "delete", "p-1"},
	}
	for _, c := range cases {
		args := append(append([]string{}, c...), "--api-url", dead, "--api-key", "k", "--org", "o-1", "--project", "p-1")
		_, errb, code := run(t, d, nil, args...)
		if code != cerr.ExitUsage || !has(errb, "--yes") {
			t.Errorf("%v: exit %d (want 2 before any network call): %s", c, code, errb)
		}
	}
	// --dry-run on member remove is read-only and must not be gated.
	_, errb, code := run(t, d, nil, "member", "remove", "u-1", "--dry-run", "--api-url", dead, "--api-key", "k", "--org", "o-1")
	if code == cerr.ExitUsage {
		t.Fatalf("--dry-run must not require --yes: %s", errb)
	}
}

// Two invocations racing on an empty cache: both may mint (the lock is not
// held across the network call), but the cache converges on ONE key and both
// runs succeed — no lost update, no lock held during the HTTP round trip.
func TestProjectKeyMintRaceConverges(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/orgs", 200, organizationsFixture())
	var mints int32
	f.handle("POST", "/projects/"+testProjP1+"/api-keys", func(w http.ResponseWriter, r *http.Request) {
		n := atomic.AddInt32(&mints, 1)
		time.Sleep(50 * time.Millisecond) // widen the race window
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(apiKeyFixture("8", testProjP1, "dtn_minted_"+strconv.Itoa(int(n)), []string{"write:sandboxes", "read:sandboxes"})))
	})
	f.on("GET", "/regions", 200, map[string]any{"regions": []any{}, "default_region_id": nil})
	f.on("GET", "/sandboxes", 200, map[string]any{"sandboxes": []any{}, "next_cursor": nil})
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	for _, key := range []string{"DAYTONA_API_URL", "DAYTONA_V2_API_URL", "DAYTONA_API_KEY", "DAYTONA_PROFILE", "DAYTONA_ORG", "DAYTONA_PROJECT", "DAYTONA_REGION"} {
		t.Setenv(key, "")
	}
	t.Setenv("DAYTONA_CONFIG_DIR", d)
	t.Setenv("CI", "1")
	var wg sync.WaitGroup
	codes := make([]int, 2)
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			_, _, codes[i] = runNoEnv(t, "sandbox", "list", "-o", "json")
		}(i)
	}
	wg.Wait()
	if codes[0] != 0 || codes[1] != 0 {
		t.Fatalf("exit codes %v", codes)
	}
	st, _ := config.Open(d)
	cr, _ := st.LoadCredentials()
	if n := len(cr.Profiles["default"].WorkloadKeys); n != 1 {
		t.Fatalf("cache must converge on one key, has %d", n)
	}
}

// Off-TTY, login must not fall into the loopback flow (nobody can
// complete the redirect); it takes the device flow.
func TestLoginOffTTYUsesDeviceFlow(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
	f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{"issuer": f.srv.URL + "/idp", "authorization_endpoint": f.srv.URL + "/idp/authorize", "token_endpoint": f.srv.URL + "/idp/token", "device_authorization_endpoint": f.srv.URL + "/idp/device"})
	f.on("POST", "/idp/device", 200, map[string]any{"device_code": "dc", "user_code": "ABCD", "verification_uri": "https://idp/activate", "expires_in": 60, "interval": 1})
	f.handle("POST", "/idp/token", func(w http.ResponseWriter, r *http.Request) {
		_ = r.ParseForm()
		if r.PostForm.Get("grant_type") != "urn:ietf:params:oauth:grant-type:device_code" {
			w.WriteHeader(400)
			_, _ = w.Write([]byte(`{"error":"invalid_grant"}`))
			return
		}
		_, _ = w.Write([]byte(`{"access_token":"jwt","refresh_token":"rt","expires_in":3600}`))
	})
	f.on("GET", "/me", 200, map[string]any{"id": testUserID, "email": "me@x"})
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgP+"/projects", 200, projectsFixture(testOrgP, "default"))
	d := cfgDir(t)
	_, errb, code := run(t, d, nil, "login", "--api-url", f.srv.URL)
	if code != 0 || !has(errb, "ABCD") {
		t.Fatalf("expected device flow: %d %s", code, errb)
	}
}

func TestLoginSameIdentityFallsBackToDefaultWhenRememberedProjectIsGone(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
	f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{"issuer": f.srv.URL + "/idp", "authorization_endpoint": f.srv.URL + "/idp/authorize", "token_endpoint": f.srv.URL + "/idp/token", "device_authorization_endpoint": f.srv.URL + "/idp/device"})
	f.on("POST", "/idp/device", 200, map[string]any{"device_code": "dc", "user_code": "ABCD", "verification_uri": "https://idp/activate", "expires_in": 60, "interval": 1})
	f.handle("POST", "/idp/token", func(w http.ResponseWriter, r *http.Request) {
		_ = r.ParseForm()
		if r.PostForm.Get("grant_type") != "urn:ietf:params:oauth:grant-type:device_code" {
			w.WriteHeader(400)
			_, _ = w.Write([]byte(`{"error":"invalid_grant"}`))
			return
		}
		_, _ = w.Write([]byte(`{"access_token":"jwt","refresh_token":"rt","expires_in":3600}`))
	})
	f.on("GET", "/me", 200, map[string]any{"id": testUserID, "email": "me@x"})
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgP+"/projects", 200, projectsFixture(testOrgP, "default"))
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	st, _ := config.Open(d)
	require.NoError(t, st.Update(func(c *config.Config, _ *config.Credentials) (bool, bool, error) {
		p, _ := c.Profile("default")
		p.ProjectByOrg[testOrgP] = testProjP2
		return true, false, nil
	}))

	_, errb, code := run(t, d, nil, "login", "--device")
	require.Equal(t, 0, code, errb)
	require.Contains(t, errb, "fell back to default project")

	cfg, err := st.LoadConfig()
	require.NoError(t, err)
	p, ok := cfg.Profile("default")
	require.True(t, ok)
	require.Equal(t, testProjP1, p.ProjectByOrg[testOrgP])
}

// No credential at all → exit 3 ("not logged in"), not exit 2 ("project
// required"): the actionable diagnosis comes first.
func TestNoCredentialIsAuthErrorBeforeProject(t *testing.T) {
	_, errb, code := run(t, cfgDir(t), nil, "sandbox", "list", "-o", "json", "--api-url", "http://127.0.0.1:1")
	if code != cerr.ExitAuth || !has(errb, "not logged in") {
		t.Fatalf("%d %s", code, errb)
	}
}

// A browser/device login on a profile that held a static credential (or with
// --api-key set) must run its own context-setup calls with the tokens it just
// saved, not the stale credential.
func TestLoginReplacesStaleStaticCredentialForContextSetup(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
	f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{"issuer": f.srv.URL + "/idp", "authorization_endpoint": f.srv.URL + "/idp/authorize", "token_endpoint": f.srv.URL + "/idp/token", "device_authorization_endpoint": f.srv.URL + "/idp/device"})
	f.on("POST", "/idp/device", 200, map[string]any{"device_code": "dc", "user_code": "ABCD", "verification_uri": "https://idp/activate", "expires_in": 60, "interval": 1})
	f.on("POST", "/idp/token", 200, map[string]any{"access_token": "jwt-new", "refresh_token": "rt", "expires_in": 3600})
	var meAuth []string
	f.handle("GET", "/me", func(w http.ResponseWriter, r *http.Request) {
		meAuth = append(meAuth, r.Header.Get("Authorization"))
		_, _ = w.Write([]byte(`{"id":"` + testUserID + `","email":"me@x"}`))
	})
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgP+"/projects", 200, projectsFixture(testOrgP, "default"))
	d := cfgDir(t)
	_, _, code := run(t, d, nil, "login", "--token", "dtn_old_static", "--api-url", f.srv.URL)
	if code != 0 {
		t.Fatal(code)
	}
	_, errb, code := run(t, d, nil, "login", "--device")
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	for _, a := range meAuth {
		if a != "Bearer jwt-new" {
			t.Fatalf("/me called with %q after login; want the new token", a)
		}
	}
	if len(meAuth) == 0 {
		t.Fatal("/me was not called")
	}
}

// whoami must not hide an /orgs failure behind a success.
func TestWhoamiSurfacesOrgsFailure(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/me", 200, map[string]any{"id": testUserID, "email": "me@x"})
	f.on("GET", "/orgs", 502, `{"code":"BAD_GATEWAY","message":"service unavailable","source":"DAYTONA_API"}`)
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	_, errb, code := run(t, d, nil, "whoami", "-o", "json")
	if code == 0 || !has(errb, "/orgs") {
		t.Fatalf("expected a failure naming /orgs, got %d %s", code, errb)
	}
}

func TestWhoamiResolvesNameOverridesToIDs(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/me", 200, map[string]any{"id": testUserID, "email": "me@x"})
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgA+"/projects", 200, projectsFixture(testOrgA, "default", "lab"))
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	stdout, stderr, code := run(t, d, nil, "--org", "Acme", "--project", "lab", "whoami", "-o", "json")
	require.Equal(t, 0, code, stderr)
	out := mustJSON(t, stdout)
	require.Equal(t, testOrgA, out["org_id"])
	require.Equal(t, testProjA2, out["project_id"])
	require.Equal(t, "Acme", out["org_ref"])
	require.Equal(t, "lab", out["project_ref"])
}

func TestWhoamiReportsUnresolvedNamesOnlyAsRefs(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/orgs", 502, `{"code":"BAD_GATEWAY","message":"service unavailable","source":"DAYTONA_API"}`)
	d := cfgDir(t)
	_, _, code := run(t, d, nil, "login", "--token", "dtn_abc", "--api-url", f.srv.URL)
	require.Equal(t, 0, code)
	stdout, stderr, code := run(t, d, nil, "--org", "Acme", "--project", "lab", "whoami", "-o", "json")
	require.Equal(t, 0, code, stderr)
	out := mustJSON(t, stdout)
	require.NotContains(t, out, "org_id", "an unresolved name is not an ID")
	require.NotContains(t, out, "project_id")
	require.Equal(t, "Acme", out["org_ref"])
	require.Equal(t, "lab", out["project_ref"])
	require.Equal(t, false, out["context_resolved"])
}

// A login that lands on an account with no organizations clears a stale
// selection instead of leaving the previous account's org in place.
func TestLoginClearsStaleOrgWhenAccountHasNone(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
	f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{"issuer": f.srv.URL + "/idp", "authorization_endpoint": f.srv.URL + "/idp/authorize", "token_endpoint": f.srv.URL + "/idp/token", "device_authorization_endpoint": f.srv.URL + "/idp/device"})
	f.on("POST", "/idp/device", 200, map[string]any{"device_code": "dc", "user_code": "ABCD", "verification_uri": "https://idp/activate", "expires_in": 60, "interval": 1})
	f.on("POST", "/idp/token", 200, map[string]any{"access_token": "jwt", "refresh_token": "rt", "expires_in": 3600})
	f.on("GET", "/me", 200, map[string]any{"id": "00000000-0000-0000-0000-00000000beef", "email": "new@x"})
	f.on("GET", "/orgs", 200, map[string]any{"organizations": []any{}})
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour) // previous account, org selected
	_, errb, code := run(t, d, nil, "login", "--device")
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	st, _ := config.Open(d)
	c, _ := st.LoadConfig()
	p, _ := c.Profile("default")
	if p.OrgID != "" {
		t.Fatalf("stale org kept: %q", p.OrgID)
	}
}

// Billing: no credential → exit 3 before "organization required" (exit 2).
func TestBillingNoCredentialIsAuthError(t *testing.T) {
	_, errb, code := run(t, cfgDir(t), nil, "billing", "wallet", "--api-url", "http://127.0.0.1:1")
	if code != cerr.ExitAuth || !has(errb, "not logged in") {
		t.Fatalf("%d %s", code, errb)
	}
}

// IdP-supplied device-login text is sanitized before it reaches the terminal.
func TestDeviceLoginPromptIsSanitized(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
	f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{"issuer": f.srv.URL + "/idp", "authorization_endpoint": f.srv.URL + "/idp/authorize", "token_endpoint": f.srv.URL + "/idp/token", "device_authorization_endpoint": f.srv.URL + "/idp/device"})
	f.on("POST", "/idp/device", 200, map[string]any{"device_code": "dc", "user_code": "AB\x1b]0;pwned\x07CD\nrun: rm -rf /", "verification_uri": "https://idp/activate\x1b[31m", "expires_in": 60, "interval": 1})
	f.on("POST", "/idp/token", 200, map[string]any{"access_token": "jwt", "refresh_token": "rt", "expires_in": 3600})
	f.on("GET", "/me", 200, map[string]any{"id": testUserID, "email": "me@x"})
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgP+"/projects", 200, projectsFixture(testOrgP, "default"))
	_, errb, code := run(t, cfgDir(t), nil, "login", "--api-url", f.srv.URL, "--device")
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	if strings.Contains(errb, "\x1b") || !strings.Contains(errb, "ABCD run: rm -rf /") || strings.Contains(errb, "\nrun:") {
		t.Fatalf("device prompt not sanitized/flattened: %q", errb)
	}
}

// --org accepts an unambiguous NAME on organization commands: it is
// resolved to the id once before hitting the UUID-keyed routes.
func TestOrgFlagAcceptsNameOnOrganizationRoutes(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgA+"/billing/wallet", 200, walletFixture())
	f.on("GET", "/orgs/"+testOrgA+"/members", 200, map[string]any{"members": []any{}})
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	_, errb, code := run(t, d, nil, "--org", "Acme", "billing", "wallet", "-o", "json")
	if code != 0 {
		t.Fatalf("billing with --org name: %d %s", code, errb)
	}
	_, errb, code = run(t, d, nil, "--org", "Acme", "member", "list", "-o", "json")
	if code != 0 {
		t.Fatalf("member list with --org name: %d %s", code, errb)
	}
	_, errb, code = run(t, d, nil, "--org", "ACME", "billing", "wallet", "-o", "json")
	if code != cerr.ExitUsage || !has(errb, "ambiguous") {
		t.Fatalf("ambiguous name must fail: %d %s", code, errb)
	}
}

// --org <name> together with --project: the project override follows the
// org once the name is resolved to its id.
func TestOrgNameOverrideKeepsProjectOverride(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgA+"/projects", 200, projectsFixture(testOrgA, "default", "lab"))
	f.on("GET", "/projects/"+testProjA2, 200, map[string]any{"id": testProjA2, "org_id": testOrgA, "name": "lab", "default": false, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"})
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	out, errb, code := run(t, d, nil, "--org", "Acme", "--project", testProjA2, "project", "get", "-o", "json")
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	if mustJSON(t, out)["id"] != testProjA2 {
		t.Fatalf("project override lost after org-name resolution: %s", out)
	}
}

// The org-name resolver surfaces real failures (401/403/5xx) instead of
// silently retrying the raw value.
func TestOrgNameResolverSurfacesAuthFailure(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/orgs", 403, `{"code":"FORBIDDEN","message":"forbidden","source":"DAYTONA_API"}`)
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	_, errb, code := run(t, d, nil, "--org", "Acme", "member", "list", "-o", "json")
	if code != cerr.ExitForbidden || !has(errb, "/orgs") {
		t.Fatalf("%d %s", code, errb)
	}
}

// A discovered OIDC client id is recorded on the tokens (for refresh), NOT
// persisted as the profile's override — a later login against another API
// URL must rediscover it.
func TestLoginDoesNotPersistDiscoveredClientIDAsOverride(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
	f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{"issuer": f.srv.URL + "/idp", "authorization_endpoint": f.srv.URL + "/idp/authorize", "token_endpoint": f.srv.URL + "/idp/token", "device_authorization_endpoint": f.srv.URL + "/idp/device"})
	f.on("POST", "/idp/device", 200, map[string]any{"device_code": "dc", "user_code": "ABCD", "verification_uri": "https://idp/activate", "expires_in": 60, "interval": 1})
	f.on("POST", "/idp/token", 200, map[string]any{"access_token": "jwt", "refresh_token": "rt", "expires_in": 3600})
	f.on("GET", "/me", 200, map[string]any{"id": testUserID, "email": "me@x"})
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgP+"/projects", 200, projectsFixture(testOrgP, "default"))
	d := cfgDir(t)
	_, errb, code := run(t, d, nil, "login", "--api-url", f.srv.URL, "--device")
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	st, _ := config.Open(d)
	c, _ := st.LoadConfig()
	p, _ := c.Profile("default")
	if p.OIDCClientID != "" {
		t.Fatalf("discovered client id persisted as a profile override: %q", p.OIDCClientID)
	}
	cr, _ := st.LoadCredentials()
	if cr.Profiles["default"].OIDC.ClientID == "" {
		t.Fatal("minting client id must be recorded on the tokens for refresh")
	}
}

// Inside an SSH session the loopback redirect is unreachable from the user's
// browser, so an interactive login takes the device flow with a note.
func TestLoginInSSHSessionUsesDeviceFlow(t *testing.T) {
	tty := true
	app.ForceTTY = &tty
	t.Cleanup(func() { app.ForceTTY = nil })
	f := newFakeAPI(t)
	f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
	f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{"issuer": f.srv.URL + "/idp", "authorization_endpoint": f.srv.URL + "/idp/authorize", "token_endpoint": f.srv.URL + "/idp/token", "device_authorization_endpoint": f.srv.URL + "/idp/device"})
	f.on("POST", "/idp/device", 200, map[string]any{"device_code": "dc", "user_code": "WXYZ", "verification_uri": "https://idp/activate", "expires_in": 60, "interval": 1})
	f.on("POST", "/idp/token", 200, map[string]any{"access_token": "jwt", "refresh_token": "rt", "expires_in": 3600})
	f.on("GET", "/me", 200, map[string]any{"id": testUserID, "email": "me@x"})
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgP+"/projects", 200, projectsFixture(testOrgP, "default"))
	d := cfgDir(t)
	t.Setenv("SSH_CONNECTION", "10.0.0.2 51234 10.0.0.1 22")
	_, errb, code := runWithStderrFile(t, d, filepath.Join(t.TempDir(), "stderr"), "login", "--api-url", f.srv.URL)
	if code != 0 || !has(errb, "SSH session detected") || !has(errb, "WXYZ") {
		t.Fatalf("%d %s", code, errb)
	}
}

func TestLoginBrowserFlagOverridesSSHDetection(t *testing.T) {
	tty := true
	app.ForceTTY = &tty
	t.Cleanup(func() { app.ForceTTY = nil })
	// The stubbed opener records the authorize URL and then plays the browser
	// by completing the loopback callback, so the test finishes without a
	// real browser and without racing on a timeout.
	prev := auth.OpenBrowser
	callbackErr := make(chan error, 1)
	auth.OpenBrowser = func(u string) error {
		go func() {
			parsed, err := url.Parse(u)
			if err != nil {
				callbackErr <- fmt.Errorf("authorize URL: %w", err)
				return
			}
			q := parsed.Query()
			resp, err := http.Get(q.Get("redirect_uri") + "?code=the-code&state=" + url.QueryEscape(q.Get("state")))
			if err != nil {
				callbackErr <- fmt.Errorf("loopback callback: %w", err)
				return
			}
			_ = resp.Body.Close()
			callbackErr <- nil
		}()
		return nil
	}
	t.Cleanup(func() { auth.OpenBrowser = prev })
	f := newFakeAPI(t)
	f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
	f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{"issuer": f.srv.URL + "/idp", "authorization_endpoint": f.srv.URL + "/idp/authorize", "token_endpoint": f.srv.URL + "/idp/token", "device_authorization_endpoint": f.srv.URL + "/idp/device"})
	f.handle("POST", "/idp/token", func(w http.ResponseWriter, r *http.Request) {
		_ = r.ParseForm()
		if r.PostForm.Get("grant_type") != "authorization_code" {
			w.WriteHeader(400)
			_, _ = w.Write([]byte(`{"error":"invalid_grant","error_description":"expected the loopback (authorization_code) flow"}`))
			return
		}
		_, _ = w.Write([]byte(`{"access_token":"jwt","refresh_token":"rt","expires_in":3600}`))
	})
	f.on("GET", "/me", 200, map[string]any{"id": testUserID, "email": "me@x"})
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgP+"/projects", 200, projectsFixture(testOrgP, "default"))
	t.Setenv("SSH_CONNECTION", "10.0.0.2 51234 10.0.0.1 22")
	_, errb, code := runWithStderrFile(t, cfgDir(t), filepath.Join(t.TempDir(), "stderr"), "login", "--api-url", f.srv.URL, "--browser")
	select {
	case err := <-callbackErr:
		if err != nil {
			t.Fatalf("browser stub failed to complete the callback: %v (login: %d %s)", err, code, errb)
		}
	case <-time.After(5 * time.Second):
		t.Fatalf("browser stub never ran: the loopback flow did not start (login: %d %s)", code, errb)
	}
	if code != 0 || has(errb, "SSH session detected") {
		t.Fatalf("--browser must keep the loopback flow inside SSH: %d %s", code, errb)
	}
	_, errb, code = run(t, cfgDir(t), nil, "login", "--api-url", f.srv.URL, "--browser", "--device")
	if code != cerr.ExitUsage {
		t.Fatalf("--browser and --device are mutually exclusive: %d %s", code, errb)
	}
}

// apiKeyFixture is the ProjectApiKeyWithToken response from POST /projects/{project_id}/api-keys:
// one place to change when the contract does.
func apiKeyFixture(idSuffix, projectID, token string, perms []string) string {
	quoted := make([]string, len(perms))
	for i, p := range perms {
		quoted[i] = `"` + p + `"`
	}
	return `{"id":"88888888-8888-8888-8888-88888888888` + idSuffix + `","name":"cli-x","project_id":"` + projectID + `","key_prefix":"dtn_","key_suffix":"xyz","token":"` + token + `","project_permissions":[` + strings.Join(quoted, ",") + `],"expires_at":"2030-01-01T00:00:00Z","created_at":"2026-01-01T00:00:00Z","revoked_at":null,"last_used_at":null,"sync_to_v0":true, "sync_to_v1":true,"service_account_id":null,"service_account_name":null}`
}

// A static credential (--api-key, DAYTONA_API_KEY, `login --token`) is sent
// as-is on every route: a 401 stands, and the CLI never mints, caches or
// substitutes a project key for it.
func TestStaticCredentialIsSentAsIs(t *testing.T) {
	for _, source := range []string{"flag", "env", "login-token"} {
		t.Run(source, func(t *testing.T) {
			f := newFakeAPI(t)
			f.on("GET", "/sandboxes/7fJk2mQpXv9dLc3RtY", 401, `{"code":"UNAUTHORIZED","message":"unauthorized","source":"DAYTONA_API"}`)
			f.on("POST", "/sandboxes", 401, `{"code":"UNAUTHORIZED","message":"unauthorized","source":"DAYTONA_API"}`)
			f.on("GET", "/orgs", 200, organizationsFixture())
			f.on("POST", "/projects/"+testProjP1+"/api-keys", 201, apiKeyFixture("8", testProjP1, "dtn_minted", []string{"write:sandboxes"}))
			d := cfgDir(t)
			env := map[string]string{}
			args := []string{"--api-url", f.srv.URL, "--org", testOrgP, "--project", testProjP1}
			switch source {
			case "flag":
				args = append(args, "--api-key", "dtno_orgkey")
			case "env":
				env["DAYTONA_API_KEY"] = "dtno_orgkey"
			case "login-token":
				_, errb, code := run(t, d, nil, "login", "--token", "dtno_orgkey", "--api-url", f.srv.URL)
				require.Equal(t, 0, code, errb)
			}
			_, errb, code := run(t, d, env, append([]string{"sandbox", "get", "7fJk2mQpXv9dLc3RtY", "-o", "json"}, args...)...)
			require.Equal(t, cerr.ExitAuth, code, errb)
			_, errb, code = run(t, d, env, append([]string{"sandbox", "create", "-o", "json"}, args...)...)
			require.Equal(t, cerr.ExitAuth, code, errb)
			for _, r := range f.requests {
				require.NotEqual(t, "POST /projects/"+testProjP1+"/api-keys", r.Method+" "+r.Path, "no project key is minted for a static credential")
				if strings.HasPrefix(r.Path, "/sandboxes") {
					require.Equal(t, "Bearer dtno_orgkey", r.Auth)
				}
			}
			creds, err := os.ReadFile(filepath.Join(d, "credentials.yaml"))
			if err == nil {
				require.NotContains(t, string(creds), "workload_keys", "no derived key is cached")
			}
		})
	}
}

// An explicitly empty --api-key is a usage error, never a fall-through to
// DAYTONA_API_KEY or the saved login.
func TestEmptyAPIKeyFlagIsAUsageError(t *testing.T) {
	d := cfgDir(t)
	for _, value := range []string{"", "   "} {
		_, errb, code := run(t, d, map[string]string{"DAYTONA_API_KEY": "fixture-env-key"}, "whoami", "--api-key", value, "-o", "json")
		require.Equal(t, cerr.ExitUsage, code, errb)
		require.Contains(t, errb, "--api-key is empty")
	}
	out, errb, code := run(t, d, map[string]string{"DAYTONA_API_KEY": "   "}, "whoami", "-o", "json")
	require.Equal(t, 0, code, errb)
	require.NotContains(t, mustJSON(t, out), "credential_source", "a blank DAYTONA_API_KEY counts as unset")
}
