// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/auth"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/stretchr/testify/require"
)

func projectsFixture(org string, names ...string) map[string]any {
	idsByOrg := map[string][]string{
		testOrgP: {testProjP1, testProjP2, "88888888-8888-8888-8888-888888888888"},
		testOrgA: {testProjA1, testProjA2, "99999999-9999-9999-9999-999999999999"},
	}
	ids := idsByOrg[org]
	ps := []map[string]any{}
	for i, n := range names {
		ps = append(ps, map[string]any{"id": ids[i], "org_id": org, "name": n, "default": n == "default", "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"})
	}
	return map[string]any{"projects": ps}
}

func TestOrgCRUDAndGet(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgP, 200, organizationFixture(testOrgP, "Me", true))
	f.on("GET", "/orgs/"+testOrgA, 200, organizationFixture(testOrgA, "Acme", false))
	f.on("POST", "/orgs", 201, organizationFixture("12345678-1234-1234-1234-123456789012", "New", false))
	f.on("PATCH", "/orgs/"+testOrgA, 200, organizationFixture(testOrgA, "Renamed", false))
	f.on("DELETE", "/orgs/"+testOrgA, 204, "")
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)

	out, _, code := run(t, d, nil, "org", "get", "-o", "json")
	if code != 0 || mustJSON(t, out)["id"] != testOrgP {
		t.Fatalf("get default org: %d %s", code, out)
	}
	out, _, code = run(t, d, nil, "org", "get", "Me", "-o", "json")
	if code != 0 || mustJSON(t, out)["id"] != testOrgP {
		t.Fatal(out)
	}
	out, _, code = run(t, d, nil, "org", "create", "New", "-o", "json")
	if code != 0 || mustJSON(t, out)["id"] != "12345678-1234-1234-1234-123456789012" {
		t.Fatal(out)
	}
	out, _, code = run(t, d, nil, "org", "update", testOrgA, "--name", "Renamed", "-o", "json")
	if code != 0 || mustJSON(t, out)["name"] != "Renamed" {
		t.Fatal(out)
	}
	_, _, code = run(t, d, nil, "org", "update", testOrgA)
	if code != cerr.ExitUsage {
		t.Fatal("--name required")
	}
	_, errb, code := run(t, d, nil, "org", "delete", testOrgA)
	if code != cerr.ExitUsage || !has(errb, "--yes") {
		t.Fatalf("%d %s", code, errb)
	}
	out, _, code = run(t, d, nil, "org", "delete", testOrgA, "--yes", "-o", "json")
	if code != 0 || !isAck(t, out, "id", testOrgA, "deleted") {
		t.Fatal(code, out)
	}
	last := f.requests[len(f.requests)-1]
	if last.Method != "DELETE" || last.Path != "/orgs/"+testOrgA {
		t.Fatalf("%+v", last)
	}
	// human table output renders the active marker
	out, _, _ = run(t, d, nil, "org", "list", "-o", "table")
	if !has(out, "*") || !has(out, "Acme") {
		t.Fatalf("table: %q", out)
	}
	out, _, _ = run(t, d, nil, "org", "list", "-o", "yaml")
	if !has(out, "items:") {
		t.Fatal(out)
	}
}

func TestPickerOptions_sanitizeServerControlledNames(t *testing.T) {
	orgs := buildOrgPickerOptions([]gen.OrganizationListItem{{Name: "Acme\nrm -rf", Personal: true}})
	require.Len(t, orgs, 1)
	require.Equal(t, "Acme rm -rf (personal)", orgs[0].Label)

	projects := buildProjectPickerOptions([]gen.Project{{Name: "Lab\nnext"}})
	require.Len(t, projects, 1)
	require.Equal(t, "Lab next", projects[0].Label)
}

func TestOrgGet_usesDetailRouteAuthorization(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgA, 403, map[string]any{"code": "FORBIDDEN", "message": "missing read:organization", "source": "DAYTONA_API"})
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	_, _, code := run(t, d, nil, "org", "get", "Acme", "-o", "json")
	if code != cerr.ExitForbidden {
		t.Fatalf("org get must honor GET /orgs/{id} auth, got %d", code)
	}
}

func TestOrgLeaveDropsKeysAndContext(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/me", 200, map[string]any{"id": testUserID, "email": "me@x"})
	f.on("GET", "/orgs/"+testOrgA+"/projects", 200, projectsFixture(testOrgA, "default"))
	f.on("DELETE", "/orgs/"+testOrgA+"/members/"+testUserID, 204, "")
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	st, _ := config.Open(d)
	_ = st.Update(func(c *config.Config, cr *config.Credentials) (bool, bool, error) {
		p, _ := c.Profile("default")
		p.OrgID = testOrgA
		p.ProjectByOrg[testOrgA] = testProjA1
		pc := cr.Profiles["default"]
		pc.WorkloadKeys = []config.WorkloadKey{{ProjectID: testProjA1, Key: "k-a", ExpiresAt: time.Now().Add(time.Hour)}, {ProjectID: testProjP1, Key: "k-p", ExpiresAt: time.Now().Add(time.Hour)}}
		cr.Profiles["default"] = pc
		return true, true, nil
	})
	_, errb, code := run(t, d, nil, "org", "leave")
	if code != cerr.ExitUsage || !has(errb, "--yes") {
		t.Fatalf("%d %s", code, errb)
	}
	out, _, code := run(t, d, nil, "org", "leave", "--yes", "-o", "json")
	if code != 0 || !isAck(t, out, "id", testOrgA, "left") {
		t.Fatalf("%d %s", code, out)
	}
	c, _ := st.LoadConfig()
	p, _ := c.Profile("default")
	if p.OrgID != "" || p.ProjectByOrg[testOrgA] != "" {
		t.Fatalf("context not cleared: %+v", p)
	}
	cr, _ := st.LoadCredentials()
	keys := cr.Profiles["default"].WorkloadKeys
	if len(keys) != 1 || keys[0].ProjectID != testProjP1 {
		t.Fatalf("left org's keys must be dropped, others kept: %+v", keys)
	}
}

func TestOrgDeleteDropsKeysTokensAndContext(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgA+"/projects", 200, projectsFixture(testOrgA, "default"))
	f.on("DELETE", "/orgs/"+testOrgA, 204, "")
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	st, _ := config.Open(d)
	_ = st.Update(func(c *config.Config, cr *config.Credentials) (bool, bool, error) {
		p, _ := c.Profile("default")
		p.OrgID = testOrgA
		p.ProjectByOrg[testOrgA] = testProjA1
		pc := cr.Profiles["default"]
		pc.WorkloadKeys = []config.WorkloadKey{{ProjectID: testProjA1, Key: "k-a", ExpiresAt: time.Now().Add(time.Hour)}, {ProjectID: testProjP1, Key: "k-p", ExpiresAt: time.Now().Add(time.Hour)}}
		pc.PlatformTokens = []config.PlatformToken{{ProjectID: testProjA1, Token: "pt-a", ExpiresAt: time.Now().Add(time.Hour)}, {ProjectID: testProjP1, Token: "pt-p", ExpiresAt: time.Now().Add(time.Hour)}}
		cr.Profiles["default"] = pc
		return true, true, nil
	})
	_, _, code := run(t, d, nil, "org", "delete", testOrgA, "--yes")
	if code != 0 {
		t.Fatalf("delete failed: %d", code)
	}
	c, _ := st.LoadConfig()
	p, _ := c.Profile("default")
	if p.OrgID != "" || p.ProjectByOrg[testOrgA] != "" {
		t.Fatalf("context not cleared: %+v", p)
	}
	cr, _ := st.LoadCredentials()
	keys := cr.Profiles["default"].WorkloadKeys
	if len(keys) != 1 || keys[0].ProjectID != testProjP1 {
		t.Fatalf("deleted org's keys must be dropped, others kept: %+v", keys)
	}
	tokens := cr.Profiles["default"].PlatformTokens
	if len(tokens) != 1 || tokens[0].ProjectID != testProjP1 {
		t.Fatalf("deleted org's platform tokens must be dropped, others kept: %+v", tokens)
	}
}

func TestProjectCommands(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgP+"/projects", 200, projectsFixture(testOrgP, "default", "lab", "Lab"))
	f.on("GET", "/projects/"+testProjP2, 200, map[string]any{"id": testProjP2, "org_id": testOrgP, "name": "lab", "default": false, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"})
	f.on("POST", "/orgs/"+testOrgP+"/projects", 201, map[string]any{"id": "12121212-1212-1212-1212-121212121212", "org_id": testOrgP, "name": "new", "default": false, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"})
	f.on("PATCH", "/projects/"+testProjP2, 200, map[string]any{"id": testProjP2, "org_id": testOrgP, "name": "renamed", "default": false, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"})
	f.on("DELETE", "/projects/"+testProjP2, 204, "")
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	st, _ := config.Open(d)
	_ = st.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles["default"]
		pc.WorkloadKeys = []config.WorkloadKey{{ProjectID: testProjP2, Key: "wk-deleted", ExpiresAt: time.Now().Add(time.Hour)}, {ProjectID: testProjP1, Key: "wk-kept", ExpiresAt: time.Now().Add(time.Hour)}}
		pc.PlatformTokens = []config.PlatformToken{{ProjectID: testProjP2, Token: "pt-deleted", ExpiresAt: time.Now().Add(time.Hour)}, {ProjectID: testProjP1, Token: "pt-kept", ExpiresAt: time.Now().Add(time.Hour)}}
		cr.Profiles["default"] = pc
		return false, true, nil
	})

	out, _, code := run(t, d, nil, "project", "list", "-o", "json")
	if code != 0 || len(mustJSON(t, out)["items"].([]any)) != 3 {
		t.Fatalf("%d %s", code, out)
	}
	out, _, _ = run(t, d, nil, "project", "list", "-o", "table")
	if !has(out, "NAME") {
		t.Fatal(out)
	}
	_, errb, code := run(t, d, nil, "project", "use", "LAB")
	if code != cerr.ExitUsage || !has(errb, "ambiguous") {
		t.Fatalf("%d %s", code, errb)
	}
	_, _, code = run(t, d, nil, "project", "use", "lab")
	if code != 0 {
		t.Fatal(code)
	}
	out, _, code = run(t, d, nil, "project", "get", "-o", "json")
	if code != 0 || mustJSON(t, out)["id"] != testProjP2 {
		t.Fatalf("selected project: %s", out)
	}
	out, _, code = run(t, d, nil, "project", "get", "lab", "-o", "json")
	if code != 0 || mustJSON(t, out)["name"] != "lab" {
		t.Fatal(out)
	}
	out, _, code = run(t, d, nil, "--project", "lab", "project", "get", "-o", "json")
	if code != 0 || mustJSON(t, out)["id"] != testProjP2 {
		t.Fatalf("--project name override must resolve within the selected org: %d %s", code, out)
	}
	out, _, code = run(t, d, nil, "project", "create", "new", "-o", "json")
	if code != 0 || mustJSON(t, out)["id"] != "12121212-1212-1212-1212-121212121212" {
		t.Fatal(out)
	}
	out, _, code = run(t, d, nil, "project", "update", "lab", "--name", "renamed", "-o", "json")
	if code != 0 || mustJSON(t, out)["name"] != "renamed" {
		t.Fatal(out)
	}
	_, _, code = run(t, d, nil, "project", "delete", "lab")
	if code != cerr.ExitUsage {
		t.Fatal("needs --yes")
	}
	out, _, code = run(t, d, nil, "project", "delete", "lab", "--yes", "-o", "json")
	if code != 0 || !isAck(t, out, "id", testProjP2, "deleted") {
		t.Fatal(code, out)
	}
	cfg, _ := st.LoadConfig()
	p, _ := cfg.Profile("default")
	if p.ProjectByOrg[testOrgP] != "" {
		t.Fatalf("deleted project must be cleared from the remembered selection: %+v", p.ProjectByOrg)
	}
	cr, _ := st.LoadCredentials()
	keys := cr.Profiles["default"].WorkloadKeys
	if len(keys) != 1 || keys[0].ProjectID != testProjP1 {
		t.Fatalf("deleted project's cached key must be dropped: %+v", keys)
	}
	tokens := cr.Profiles["default"].PlatformTokens
	if len(tokens) != 1 || tokens[0].ProjectID != testProjP1 {
		t.Fatalf("deleted project's platform token must be dropped: %+v", tokens)
	}
	_, _, code = run(t, d, nil, "project", "get", "nope")
	if code != cerr.ExitNotFound {
		t.Fatal(code)
	}
	// non-interactive with no project selected → usage error naming the flag
	_ = st.Update(func(c *config.Config, _ *config.Credentials) (bool, bool, error) {
		p, _ := c.Profile("default")
		p.ProjectByOrg = nil
		return true, false, nil
	})
	_, errb, code = run(t, d, nil, "project", "get")
	if code != cerr.ExitUsage || !has(errb, "--project") {
		t.Fatalf("%d %s", code, errb)
	}
	// --project override does not persist
	_, _, code = run(t, d, nil, "project", "get", "--project", testProjP2, "-o", "json")
	if code != 0 {
		t.Fatal(code)
	}
	c, _ := st.LoadConfig()
	if p, _ := c.Profile("default"); p.ProjectID() != "" {
		t.Fatal("--project must not persist")
	}
}

func TestOrgLeaveHelpDocumentsFallbackCleanup(t *testing.T) {
	stdout, stderr, code := run(t, cfgDir(t), nil, "org", "leave", "--help")
	if code != 0 {
		t.Fatalf("%d %s", code, stderr)
	}
	if !has(stdout, "cannot list the org's projects") || !has(stdout, "daytona logout") {
		t.Fatalf("help must document cleanup fallback: %s", stdout)
	}
}

func TestProfileCommands(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	_, _, _ = run(t, d, nil, "login", "--token", "k1", "--api-url", f.srv.URL)
	_, _, code := run(t, d, nil, "login", "--token", "k2", "--api-url", f.srv.URL, "--profile", "work")
	if code != 0 {
		t.Fatal(code)
	}
	out, _, code := run(t, d, nil, "profile", "list", "-o", "json")
	if code != 0 || len(mustJSON(t, out)["items"].([]any)) != 2 {
		t.Fatalf("%d %s", code, out)
	}
	out, _, _ = run(t, d, nil, "profile", "list", "-o", "table")
	if !has(out, "work") || !has(out, "*") {
		t.Fatal(out)
	}
	_, _, code = run(t, d, nil, "profile", "use", "nope")
	if code != cerr.ExitNotFound {
		t.Fatal(code)
	}
	_, _, code = run(t, d, nil, "profile", "use", "work")
	if code != 0 {
		t.Fatal(code)
	}
	out, _, _ = run(t, d, nil, "whoami", "-o", "json")
	if mustJSON(t, out)["profile"] != "work" {
		t.Fatal(out)
	}
	// DAYTONA_PROFILE beats the active profile
	out, _, _ = run(t, d, map[string]string{"DAYTONA_PROFILE": "default"}, "whoami", "-o", "json")
	if mustJSON(t, out)["profile"] != "default" {
		t.Fatal(out)
	}
	_, _, code = run(t, d, nil, "profile", "delete", "nope", "--yes")
	if code != cerr.ExitNotFound {
		t.Fatal(code)
	}
	// config get/set/path
	out, _, _ = run(t, d, nil, "config", "get", "confirm", "-o", "table")
	if strings.TrimSpace(out) != "true" {
		t.Fatal(out)
	}
	// Off a terminal the default output is JSON.
	out, _, _ = run(t, d, nil, "config", "get", "confirm")
	if mustJSON(t, out)["confirm"] != true {
		t.Fatal(out)
	}
	// A profile named explicitly must exist.
	_, errb, code := run(t, d, nil, "whoami", "--profile", "ghost")
	if code != cerr.ExitNotFound || !has(errb, `no profile "ghost"`) {
		t.Fatalf("%d %s", code, errb)
	}
	_, _, code = run(t, d, map[string]string{"DAYTONA_PROFILE": "ghost"}, "whoami")
	if code != cerr.ExitNotFound {
		t.Fatal(code)
	}
	_, _, code = run(t, d, nil, "config", "set", "confirm", "false")
	if code != 0 {
		t.Fatal(code)
	}
	out, _, _ = run(t, d, nil, "config", "get", "confirm", "-o", "json")
	if mustJSON(t, out)["confirm"] != false {
		t.Fatal(out)
	}
	_, _, code = run(t, d, nil, "config", "set", "confirm", "maybe")
	if code != cerr.ExitUsage {
		t.Fatal(code)
	}
	_, _, code = run(t, d, nil, "config", "get", "nope")
	if code != cerr.ExitUsage {
		t.Fatal(code)
	}
	out, _, _ = run(t, d, nil, "config", "path", "-o", "table")
	if strings.TrimSpace(out) != d {
		t.Fatal(out)
	}
	// confirm=false turns terminal prompts off; off a terminal a destructive
	// verb still needs --yes.
	_, errb, code = run(t, d, nil, "profile", "delete", "work")
	if code != cerr.ExitUsage || !has(errb, "--yes") {
		t.Fatalf("confirm=false must not consent off a terminal: %d %s", code, errb)
	}
	_, _, code = run(t, d, nil, "profile", "delete", "work", "--yes")
	if code != 0 {
		t.Fatal(code)
	}
}

// Browser (PKCE) login end to end against a fake IdP: the CLI opens a loopback
// listener; the test plays the browser by following the printed URL's
// redirect_uri with a code, then the CLI exchanges it and sets up context.
// The loopback flow is interactive-only (off-TTY the CLI takes the device
// flow — TestLoginOffTTYUsesDeviceFlow), so the test forces a TTY.
func TestLoginBrowserFlow(t *testing.T) {
	tty := true
	app.ForceTTY = &tty
	t.Cleanup(func() { app.ForceTTY = nil })
	// Never fork a real browser from a test: the opener is a seam.
	prev := auth.OpenBrowser
	auth.OpenBrowser = func(string) error { return errors.New("no browser in tests") }
	t.Cleanup(func() { auth.OpenBrowser = prev })
	f := newFakeAPI(t)
	f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
	f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{"issuer": f.srv.URL + "/idp", "authorization_endpoint": f.srv.URL + "/idp/authorize", "token_endpoint": f.srv.URL + "/idp/token"})
	var mu sync.Mutex
	var seenVerifier string
	f.handle("POST", "/idp/token", func(w http.ResponseWriter, r *http.Request) {
		_ = r.ParseForm()
		mu.Lock()
		seenVerifier = r.PostForm.Get("code_verifier")
		mu.Unlock()
		if r.PostForm.Get("grant_type") != "authorization_code" || r.PostForm.Get("code") != "the-code" || r.PostForm.Get("client_id") != "native" {
			w.WriteHeader(400)
			_, _ = w.Write([]byte(`{"error":"invalid_grant","error_description":"` + r.PostForm.Encode() + `"}`))
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"access_token": "jwt-1", "refresh_token": "rt-1", "expires_in": 3600})
	})
	f.on("GET", "/me", 200, map[string]any{"id": testUserID, "email": "me@x"})
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgP+"/projects", 200, projectsFixture(testOrgP, "default", "other"))
	d := cfgDir(t)

	// Drive the browser: poll the CLI's stderr for the authorize URL, then hit
	// its redirect_uri with code+state.
	var browser sync.WaitGroup
	browser.Add(1)
	errFile := filepath.Join(t.TempDir(), "stderr")
	go func() {
		defer browser.Done()
		deadline := time.Now().Add(10 * time.Second)
		for time.Now().Before(deadline) {
			b, _ := os.ReadFile(errFile)
			for _, line := range strings.Split(string(b), "\n") {
				if strings.HasPrefix(line, f.srv.URL+"/idp/authorize?") {
					u, _ := url.Parse(line)
					q := u.Query()
					redirect := q.Get("redirect_uri")
					if q.Get("code_challenge_method") != "S256" || q.Get("audience") != "aud" || !strings.Contains(q.Get("scope"), "offline_access") {
						t.Errorf("authorize params: %v", q)
					}
					resp, err := http.Get(redirect + "?code=the-code&state=" + url.QueryEscape(q.Get("state")))
					if err == nil {
						_ = resp.Body.Close()
					}
					return
				}
			}
			time.Sleep(20 * time.Millisecond)
		}
		t.Error("never saw the authorize URL")
	}()
	// run() captures stderr in memory; tee it to a file the browser goroutine can poll.
	stdout, stderr, code := runWithStderrFile(t, d, errFile, "login", "--api-url", f.srv.URL)
	browser.Wait()
	if code != 0 {
		t.Fatalf("login: %d\n%s\n%s", code, stdout, stderr)
	}
	mu.Lock()
	if seenVerifier == "" {
		t.Fatal("no PKCE verifier sent")
	}
	mu.Unlock()
	st, _ := config.Open(d)
	c, _ := st.LoadConfig()
	p, ok := c.Profile("default")
	if !ok || p.Credential != "oidc" || p.OrgID != testOrgP || p.ProjectByOrg[testOrgP] != testProjP1 || p.Identity != testUserID {
		t.Fatalf("context after login: %+v", p)
	}
	cr, _ := st.LoadCredentials()
	if cr.Profiles["default"].OIDC.RefreshToken != "rt-1" {
		t.Fatal("tokens not saved")
	}
	// Second login, same identity: context preserved even if we pick another project first.
	_, _, _ = run(t, d, nil, "project", "use", "other")
	browser.Add(1)
	_ = os.Remove(errFile)
	go func() {
		defer browser.Done()
		deadline := time.Now().Add(10 * time.Second)
		for time.Now().Before(deadline) {
			b, _ := os.ReadFile(errFile)
			for _, line := range strings.Split(string(b), "\n") {
				if strings.HasPrefix(line, f.srv.URL+"/idp/authorize?") {
					u, _ := url.Parse(line)
					resp, err := http.Get(u.Query().Get("redirect_uri") + "?code=the-code&state=" + url.QueryEscape(u.Query().Get("state")))
					if err == nil {
						_ = resp.Body.Close()
					}
					return
				}
			}
			time.Sleep(20 * time.Millisecond)
		}
	}()
	_, _, code = runWithStderrFile(t, d, errFile, "login")
	browser.Wait()
	if code != 0 {
		t.Fatal(code)
	}
	c, _ = st.LoadConfig()
	p, _ = c.Profile("default")
	if p.ProjectByOrg[testOrgP] != testProjP2 {
		t.Fatalf("re-login must keep the selected project: %+v", p)
	}
	browser.Add(1)
	_ = os.Remove(errFile)
	go func() {
		defer browser.Done()
		deadline := time.Now().Add(10 * time.Second)
		for time.Now().Before(deadline) {
			b, _ := os.ReadFile(errFile)
			for _, line := range strings.Split(string(b), "\n") {
				if strings.HasPrefix(line, f.srv.URL+"/idp/authorize?") {
					u, _ := url.Parse(line)
					redirect := u.Query().Get("redirect_uri")
					resp, err := http.Get(redirect + "?code=the-code&state=WRONG")
					if err == nil {
						if resp.StatusCode != http.StatusBadRequest {
							t.Errorf("wrong-state callback status = %d", resp.StatusCode)
						}
						_ = resp.Body.Close()
					}
					resp, err = http.Get(redirect + "?code=the-code&state=" + url.QueryEscape(u.Query().Get("state")))
					if err == nil {
						_ = resp.Body.Close()
					}
					return
				}
			}
			time.Sleep(20 * time.Millisecond)
		}
	}()
	_, stderr, code = runWithStderrFile(t, d, errFile, "login")
	browser.Wait()
	if code != 0 {
		t.Fatalf("%d %s", code, stderr)
	}
}

// runWithStderrFile is run() with stderr additionally streamed to a file so a
// concurrent goroutine can observe it while the command blocks.
func runWithStderrFile(t *testing.T, cfgDir, path string, args ...string) (string, string, int) {
	t.Helper()
	t.Setenv("DAYTONA_CONFIG_DIR", cfgDir)
	t.Setenv("CI", "") // interactive: the browser flow under test is TTY-only
	for _, key := range []string{"DAYTONA_API_URL", "DAYTONA_V2_API_URL", "DAYTONA_API_KEY", "DAYTONA_PROFILE", "DAYTONA_ORG", "DAYTONA_PROJECT"} {
		t.Setenv(key, "")
	}
	fh, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	defer fh.Close()
	var out strings.Builder
	var errb strings.Builder
	w := &teeWriter{a: &errb, b: fh}
	root := NewRoot(&out, w)
	root.SetArgs(args)
	runErr := root.Execute()
	code := 0
	if runErr != nil {
		printError(w, runErr)
		code = cerr.ExitCode(runErr)
	}
	return out.String(), errb.String(), code
}

type teeWriter struct {
	a *strings.Builder
	b *os.File
}

func (t *teeWriter) Write(p []byte) (int, error) {
	t.a.Write(p)
	_, _ = t.b.Write(p)
	_ = t.b.Sync()
	return len(p), nil
}

// An org API key is admitted on `/orgs/{org_id}/audit-logs` but not on `GET /orgs`,
// so `--org <name>` cannot be resolved for it: a usage error naming the fix,
// not "credential rejected — run daytona login".
func TestOrgNameWithStaticCredentialThatCannotListOrgs(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/orgs", 401, map[string]any{"code": "UNAUTHORIZED", "message": "unauthorized", "source": "DAYTONA_API"})
	_, errb, code := run(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "dtno_orgkey", "-o", "json", "audit", "list", "--org", "Acme")
	if code != cerr.ExitUsage || !has(errb, "cannot list organizations") || !has(errb, "UUID") || has(errb, "daytona login") {
		t.Fatalf("%d %s", code, errb)
	}
	// An id needs no resolution and reaches the audit route as-is.
	f.on("GET", "/orgs/"+testOrgA+"/audit-logs", 200, map[string]any{"audit_logs": []any{}, "next_cursor": nil})
	_, errb, code = run(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "dtno_orgkey", "-o", "json", "audit", "list", "--org", testOrgA)
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
}
