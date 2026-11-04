// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/stretchr/testify/require"
)

// issuerFake serves the RFC 8414 metadata that marks a deployment as a
// platform-token issuer plus a POST /auth/token that mints "pt_<n>" tokens,
// recording every exchange.
type issuerFake struct {
	mu        sync.Mutex
	exchanges []map[string]any
	tokens    []string
}

func installIssuer(t *testing.T, f *fakeAPI, expiresIn int) *issuerFake {
	t.Helper()
	is := &issuerFake{}
	f.on("GET", "/.well-known/oauth-authorization-server", 200, map[string]any{"issuer": f.srv.URL, "jwks_uri": f.srv.URL + "/.well-known/jwks.json", "token_endpoint": f.srv.URL + "/auth/token"})
	f.handle("POST", "/auth/token", func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		is.mu.Lock()
		is.exchanges = append(is.exchanges, body)
		n := len(is.exchanges)
		tok := "pt_" + strings.Repeat("x", n)
		is.tokens = append(is.tokens, tok)
		is.mu.Unlock()
		if r.Header.Get("Authorization") != "Bearer jwt" || body["subject_token"] != "jwt" || body["grant_type"] != "urn:ietf:params:oauth:grant-type:token-exchange" || body["project_id"] != testProjP1 {
			w.WriteHeader(400)
			_, _ = w.Write([]byte(`{"code":"BAD_REQUEST","message":"bad exchange","source":"DAYTONA_API"}`))
			return
		}
		_, _ = w.Write([]byte(`{"access_token":"` + tok + `","issued_token_type":"urn:ietf:params:oauth:token-type:access_token","token_type":"Bearer","expires_in":` + jsonInt(expiresIn) + `}`))
	})
	return is
}

func jsonInt(n int) string { b, _ := json.Marshal(n); return string(b) }

// On an issuer deployment a login-sourced profile reaches /sandboxes with a
// platform token exchanged from its JWT —
// no project key is minted for sandbox API calls — and the token is reused
// across invocations until it nears expiry.
func TestLoginUsesPlatformTokenOnIssuerAndCachesIt(t *testing.T) {
	f := newFakeAPI(t)
	is := installIssuer(t, f, 300)
	f.on("GET", "/regions", 200, map[string]any{"regions": []any{}, "default_region_id": nil})
	mints := 0
	f.handle("POST", "/projects/"+testProjP1+"/api-keys", func(w http.ResponseWriter, _ *http.Request) { mints++; w.WriteHeader(500) })
	f.handle("GET", "/sandboxes", func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer pt_") {
			w.WriteHeader(401)
			_, _ = w.Write([]byte(`{"code":"UNAUTHORIZED","message":"unauthorized","source":"DAYTONA_API"}`))
			return
		}
		_, _ = w.Write([]byte(`{"sandboxes":[],"next_cursor":null}`))
	})
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	for i := 0; i < 3; i++ {
		if _, errb, code := run(t, d, nil, "sandbox", "list", "-o", "json"); code != 0 {
			t.Fatalf("run %d: %d %s", i, code, errb)
		}
	}
	if len(is.exchanges) != 1 {
		t.Fatalf("expected one exchange across three invocations, got %d", len(is.exchanges))
	}
	if mints != 0 {
		t.Fatalf("no project key may be minted for sandbox API calls on an issuer, got %d mints", mints)
	}
	for _, r := range f.requests {
		if r.Path == "/orgs" {
			t.Fatal("the platform-token path must not read GET /orgs")
		}
	}
	st, _ := config.Open(d)
	cr, _ := st.LoadCredentials()
	pts := cr.Profiles["default"].PlatformTokens
	if len(pts) != 1 || pts[0].ProjectID != testProjP1 || pts[0].Token != "pt_x" || time.Until(pts[0].ExpiresAt) < 4*time.Minute {
		t.Fatalf("cached platform token: %+v", pts)
	}
}

// A cached token the server rejects (401: expired between check and use, or
// the signing key rotated) is dropped and exchanged once more; a 403 is a
// permission answer and stands.
func TestPlatformTokenRejectedIsReexchangedOnce(t *testing.T) {
	f := newFakeAPI(t)
	is := installIssuer(t, f, 300)
	f.on("GET", "/regions", 200, map[string]any{"regions": []any{}, "default_region_id": nil})
	var auths []string
	f.handle("GET", "/sandboxes", func(w http.ResponseWriter, r *http.Request) {
		a := r.Header.Get("Authorization")
		auths = append(auths, a)
		switch a {
		case "Bearer pt_xx":
			_, _ = w.Write([]byte(`{"sandboxes":[],"next_cursor":null}`))
		default:
			w.WriteHeader(401)
			_, _ = w.Write([]byte(`{"code":"UNAUTHORIZED","message":"token expired","source":"DAYTONA_API"}`))
		}
	})
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	_, errb, code := run(t, d, nil, "sandbox", "list", "-o", "json")
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	if len(is.exchanges) != 2 || len(auths) != 2 || auths[0] != "Bearer pt_x" || auths[1] != "Bearer pt_xx" {
		t.Fatalf("expected first token rejected then one re-exchange: exchanges=%d auths=%v", len(is.exchanges), auths)
	}
	st, _ := config.Open(d)
	cr, _ := st.LoadCredentials()
	if pts := cr.Profiles["default"].PlatformTokens; len(pts) != 1 || pts[0].Token != "pt_xx" {
		t.Fatalf("the rejected token must be gone and the fresh one cached: %+v", pts)
	}

	// 403 stands: no re-exchange, exit 4.
	f2 := newFakeAPI(t)
	is2 := installIssuer(t, f2, 300)
	f2.on("GET", "/regions", 200, map[string]any{"regions": []any{}, "default_region_id": nil})
	f2.on("DELETE", "/sandboxes/7fJk2mQpXv9dLc3RtY", 403, map[string]any{"code": "FORBIDDEN", "message": "permission denied", "source": "DAYTONA_API"})
	f2.on("GET", "/sandboxes/7fJk2mQpXv9dLc3RtY", 200, sandboxDetailFixture("7fJk2mQpXv9dLc3RtY", "linux", "running"))
	d2 := cfgDir(t)
	oidcProfile(t, d2, f2.srv.URL, time.Hour)
	_, errb, code = run(t, d2, nil, "sandbox", "delete", "7fJk2mQpXv9dLc3RtY", "--yes", "--no-wait", "-o", "json")
	if code != cerr.ExitForbidden || len(is2.exchanges) != 1 {
		t.Fatalf("a 403 under a platform token stands (exit 4, one exchange): %d %s exchanges=%d", code, errb, len(is2.exchanges))
	}
}

// Under a login, /sandboxes is reached with the platform token and toolbox requests with
// the login's minted key — never the platform token, never the JWT.
func TestToolboxUsesProjectKeyWhileAPIUsesPlatformToken(t *testing.T) {
	f := newFakeAPI(t)
	installIssuer(t, f, 300)
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("POST", "/projects/"+testProjP1+"/api-keys", 201, apiKeyFixture("8", testProjP1, "dtn_minted", []string{"write:sandboxes", "read:sandboxes"}))
	f.handle("GET", "/sandboxes/sbx", func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer pt_") {
			w.WriteHeader(401)
			_, _ = w.Write([]byte(`{"code":"UNAUTHORIZED","message":"unauthorized","source":"DAYTONA_API"}`))
			return
		}
		_, _ = w.Write([]byte(`{"id":"sbx","class":"linux","name":"box","status":"running","region_id":"r1","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"spot":false,"port_map":[],"disk_mib":1024,"memory_mib":512,"cpu":1,"cores_max":1,"toolbox_proxy_url":"` + f.srv.URL + `/test-tools"}`))
	})
	f.handle("GET", "/test-tools/sbx/port", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer dtn_minted" {
			w.WriteHeader(401)
			_, _ = w.Write([]byte(`{"code":"UNAUTHORIZED","message":"unauthorized","source":"DAYTONA_PROXY"}`))
			return
		}
		_, _ = w.Write([]byte(`{"ports":[8080]}`))
	})
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	_, errb, code := run(t, d, nil, "sandbox", "port", "list", "sbx", "-o", "json")
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	var sandboxCalls, toolboxCalls int
	for _, r := range f.requests {
		switch {
		case r.Path == "/sandboxes/sbx":
			sandboxCalls++
			if !strings.HasPrefix(r.Auth, "Bearer pt_") {
				t.Fatalf("/sandboxes/sbx sent %q, want the platform token", r.Auth)
			}
		case strings.HasPrefix(r.Path, "/test-tools/"):
			toolboxCalls++
			if r.Auth != "Bearer dtn_minted" {
				t.Fatalf("%s sent %q, want the minted project key", r.Path, r.Auth)
			}
		}
	}
	if sandboxCalls == 0 || toolboxCalls == 0 {
		t.Fatalf("expected sandbox and toolbox calls, sandbox=%d toolbox=%d", sandboxCalls, toolboxCalls)
	}
}

// A project the caller is not a member of is a 404 from the exchange; the CLI
// names the remedy instead of relaying "not found".
func TestPlatformTokenExchangeNotMemberIsExplained(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/.well-known/oauth-authorization-server", 200, map[string]any{"issuer": f.srv.URL})
	f.on("POST", "/auth/token", 404, map[string]any{"code": "NOT_FOUND", "message": "not one you are a member of", "source": "DAYTONA_API"})
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	_, errb, code := run(t, d, nil, "sandbox", "list", "-o", "json")
	if code != cerr.ExitNotFound || !has(errb, "not one you are a member of") || !has(errb, "project use") {
		t.Fatalf("%d %s", code, errb)
	}
}

// `org leave` forgets the platform tokens of the projects it drops, like the
// minted keys.
func TestOrgLeaveDropsPlatformTokens(t *testing.T) {
	d := cfgDir(t)
	f := newFakeAPI(t)
	st, _ := config.Open(d)
	_ = st.Update(func(c *config.Config, cr *config.Credentials) (bool, bool, error) {
		c.Upsert(config.Profile{Name: "default", APIURL: f.srv.URL, Credential: "oidc", Identity: testUserID, OrgID: testOrgP, ProjectByOrg: map[string]string{testOrgP: testProjP1}})
		c.ActiveProfile = "default"
		cr.Profiles["default"] = config.ProfileCredentials{
			OIDC:           &config.Tokens{AccessToken: "jwt", RefreshToken: "rt", ExpiresAt: time.Now().Add(time.Hour)},
			PlatformTokens: []config.PlatformToken{{ProjectID: testProjP1, Token: "pt_stale", ExpiresAt: time.Now().Add(4 * time.Minute)}, {ProjectID: testProjA1, Token: "pt_other", ExpiresAt: time.Now().Add(4 * time.Minute)}},
		}
		return true, true, nil
	})
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgP+"/projects", 200, projectsFixture(testOrgP, "default"))
	f.on("GET", "/me", 200, map[string]any{"id": testUserID, "email": "me@x"})
	f.on("DELETE", "/orgs/"+testOrgP+"/members/"+testUserID, 204, "")
	_, errb, code := run(t, d, nil, "org", "leave", testOrgP, "--yes")
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	cr, _ := st.LoadCredentials()
	pts := cr.Profiles["default"].PlatformTokens
	if len(pts) != 1 || pts[0].Token != "pt_other" {
		t.Fatalf("expected only the other org's token to survive: %+v", pts)
	}
}

func TestOrgLeaveFallbackDropsRememberedProjectWhenProjectLookupFails(t *testing.T) {
	d := cfgDir(t)
	f := newFakeAPI(t)
	st, _ := config.Open(d)
	_ = st.Update(func(c *config.Config, cr *config.Credentials) (bool, bool, error) {
		c.Upsert(config.Profile{Name: "default", APIURL: f.srv.URL, Credential: "oidc", Identity: testUserID, OrgID: testOrgP, ProjectByOrg: map[string]string{testOrgP: testProjP1}})
		c.ActiveProfile = "default"
		cr.Profiles["default"] = config.ProfileCredentials{
			OIDC:           &config.Tokens{AccessToken: "jwt", RefreshToken: "rt", ExpiresAt: time.Now().Add(time.Hour)},
			WorkloadKeys:   []config.WorkloadKey{{ProjectID: testProjP1, Key: "wk-stale", ExpiresAt: time.Now().Add(time.Hour)}, {ProjectID: testProjA1, Key: "wk-other", ExpiresAt: time.Now().Add(time.Hour)}},
			PlatformTokens: []config.PlatformToken{{ProjectID: testProjP1, Token: "pt-stale", ExpiresAt: time.Now().Add(time.Hour)}, {ProjectID: testProjA1, Token: "pt-other", ExpiresAt: time.Now().Add(time.Hour)}},
		}
		return true, true, nil
	})
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgP+"/projects", 500, map[string]any{"code": "INTERNAL_SERVER_ERROR", "message": "boom", "source": "DAYTONA_API"})
	f.on("GET", "/me", 200, map[string]any{"id": testUserID, "email": "me@x"})
	f.on("DELETE", "/orgs/"+testOrgP+"/members/"+testUserID, 204, "")
	_, errb, code := run(t, d, nil, "org", "leave", testOrgP, "--yes")
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	if !has(errb, "could not list its projects during cleanup") || !has(errb, "daytona logout") {
		t.Fatalf("expected fallback warning, got %s", errb)
	}
	cr, _ := st.LoadCredentials()
	keys := cr.Profiles["default"].WorkloadKeys
	if len(keys) != 1 || keys[0].ProjectID != testProjA1 {
		t.Fatalf("expected only the other organization's project key to survive: %+v", keys)
	}
	pts := cr.Profiles["default"].PlatformTokens
	if len(pts) != 1 || pts[0].ProjectID != testProjA1 {
		t.Fatalf("expected only other org platform token to survive: %+v", pts)
	}
}

func TestOrgLeaveReportsPartialFailureWhenLocalCleanupFails(t *testing.T) {
	d := cfgDir(t)
	f := newFakeAPI(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgP+"/projects", 200, projectsFixture(testOrgP, "default"))
	f.on("GET", "/me", 200, map[string]any{"id": testUserID, "email": "me@x"})
	f.handle(http.MethodDelete, "/orgs/"+testOrgP+"/members/"+testUserID, func(w http.ResponseWriter, _ *http.Request) {
		require.NoError(t, os.RemoveAll(d))
		w.WriteHeader(http.StatusNoContent)
	})
	_, errb, code := run(t, d, nil, "org", "leave", testOrgP, "--yes")
	if code != cerr.ExitFailure || !has(errb, "left organization "+testOrgP+" on the server") || !has(errb, "daytona logout") {
		t.Fatalf("%d %s", code, errb)
	}
	if _, statErr := os.Stat(filepath.Join(d, "config.yaml")); !os.IsNotExist(statErr) {
		t.Fatalf("expected config dir removal to trigger the local cleanup failure, got %v", statErr)
	}
}

// `--project default` (the account-command alias, and also the personal
// org's default project NAME) is resolved to the project's UUID before the
// exchange: POST /auth/token accepts ids only.
func TestPlatformTokenExchangeResolvesDefaultProjectAlias(t *testing.T) {
	f := newFakeAPI(t)
	is := installIssuer(t, f, 300)
	f.on("GET", "/regions", 200, map[string]any{"regions": []any{}, "default_region_id": nil})
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgP+"/projects", 200, projectsFixture(testOrgP, "default"))
	f.handle("GET", "/sandboxes", func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer pt_") {
			w.WriteHeader(401)
			_, _ = w.Write([]byte(`{"code":"UNAUTHORIZED","message":"unauthorized","source":"DAYTONA_API"}`))
			return
		}
		_, _ = w.Write([]byte(`{"sandboxes":[],"next_cursor":null}`))
	})
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	for i := 0; i < 2; i++ {
		_, errb, code := run(t, d, nil, "sandbox", "list", "-o", "json", "--org", testOrgP, "--project", "default")
		if code != 0 {
			t.Fatalf("run %d: %d %s", i, code, errb)
		}
	}
	// One exchange across two invocations: the alias resolves to the same
	// UUID the cache is keyed by, so the second run reuses the token.
	if len(is.exchanges) != 1 || is.exchanges[0]["project_id"] != testProjP1 {
		t.Fatalf("the exchange must carry the resolved UUID and happen once: %v", is.exchanges)
	}
}

// A project-bound static key with `DAYTONA_PROJECT=<name>`
// and no org selected passes through — the resolver has nothing to
// resolve against and passes the override through, never building
// `/orgs//projects`.
func TestStaticKeyWithProjectNameAndNoOrgPassesThrough(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/regions", 200, map[string]any{"regions": []any{}, "default_region_id": nil})
	f.handle("GET", "/sandboxes", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"sandboxes":[],"next_cursor":null}`))
	})
	_, errb, code := run(t, cfgDir(t), nil, "sandbox", "list", "-o", "json", "--api-url", f.srv.URL, "--api-key", "dtn_projectkey", "--project", "my-proj")
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	for _, r := range f.requests {
		if strings.Contains(r.Path, "/orgs/") {
			t.Fatalf("no org lookup may be attempted without an org: %s %s", r.Method, r.Path)
		}
	}
}

// `--project default` is canonicalised to the org's default-project UUID on
// the KEY path too (non-issuer deployment), so switching --org never hands
// back the previous org's default-project key, and a mixed-case UUID hits the
// same cache entry as its lowercase spelling.
func TestProjectKeyCacheKeyedByCanonicalProjectID(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/regions", 200, map[string]any{"regions": []any{}, "default_region_id": nil})
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgP+"/projects", 200, projectsFixture(testOrgP, "default"))
	f.on("GET", "/orgs/"+testOrgA+"/projects", 200, projectsFixture(testOrgA, "default"))
	mints := map[string]int{}
	for _, p := range []string{testProjP1, testProjA1} {
		project := p
		f.handle("POST", "/projects/"+project+"/api-keys", func(w http.ResponseWriter, _ *http.Request) {
			mints[project]++
			w.WriteHeader(http.StatusCreated)
			_, _ = w.Write([]byte(apiKeyFixture("8", project, "dtn_"+project[:8], []string{"write:sandboxes"})))
		})
	}
	f.handle("GET", "/sandboxes", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"sandboxes":[],"next_cursor":null}`))
	})
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	run1 := func(args ...string) {
		_, errb, code := run(t, d, nil, append([]string{"sandbox", "list", "-o", "json"}, args...)...)
		if code != 0 {
			t.Fatalf("%v: %d %s", args, code, errb)
		}
	}
	run1("--org", testOrgP, "--project", "default")
	run1("--org", testOrgA, "--project", "default")                   // other org: its OWN default project, a second mint
	run1("--org", testOrgP, "--project", strings.ToUpper(testProjP1)) // same project, different spelling: cache hit
	if mints[testProjP1] != 1 || mints[testProjA1] != 1 {
		t.Fatalf("expected one mint per project, got %v", mints)
	}
}
