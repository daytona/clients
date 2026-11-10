// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
)

func limitsFixture() map[string]any {
	usageLimit := func(usage int, limit any) map[string]any { return map[string]any{"usage": usage, "limit": limit} }
	concurrent, total, snapshots := int64(10), int64(50), int64(100)
	return map[string]any{
		// Generated types supply every required contract field, including
		// fields that this CLI does not display in its table.
		"sandboxes": gen.SandboxLimits{
			Concurrent: gen.UsageLimit{Usage: 4, Limit: &concurrent},
			Total:      gen.UsageLimit{Usage: 9, Limit: &total},
		},
		"sandbox_resources": map[string]any{"max_cores": 2.5, "max_memory_mib": 8192},
		"image_builds":      map[string]any{"concurrent": usageLimit(1, nil)},
		"members":           map[string]any{"usage": 3, "limit": 5, "pending_invitations": 1},
		"storage": gen.StorageLimits{
			Images:    gen.UsageLimit{Usage: 5},
			Snapshots: gen.UsageLimit{Usage: 7, Limit: &snapshots},
		},
		"secrets_per_project":       usageLimit(2, 100),
		"api_keys_per_project":      usageLimit(1, 10),
		"api_keys_per_organization": usageLimit(3, 25),
	}
}

// `org limits` reads GET /orgs/{id}/limits for the selected org (id or name)
// and renders every limit as "current / max (source)".
func TestOrgLimits(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgA+"/limits", 200, limitsFixture())
	// The static credentials below are not project keys to the server.
	f.on("GET", "/api-keys/current", 401, map[string]any{"code": "UNAUTHORIZED", "message": "unauthorized", "source": "DAYTONA_API"})

	out, errb, code := runMgmt(t, f, d, "org", "limits", "Acme")
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	var got map[string]any
	if err := json.Unmarshal([]byte(out), &got); err != nil {
		t.Fatal(err)
	}
	if got["sandboxes"] == nil {
		t.Fatalf("json payload is the wire body: %s", out)
	}

	out, errb, code = run(t, d, nil, "--api-url", f.srv.URL, "--api-key", "k", "-o", "table", "org", "limits", "--org", testOrgA)
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	for _, want := range []string{"Concurrent sandboxes", "4 / 10", "9 / 50", "1 / unlimited", "Members", "3 + 1 pending / 5",
		"Cores per sandbox", "max 2.5", "Memory per sandbox (MiB)", "max 8192",
		"Images", "5 / unlimited", "Snapshots", "7 / 100"} {
		if !has(out, want) {
			t.Fatalf("table lacks %q:\n%s", want, out)
		}
	}
	if has(out, "Usage in") {
		t.Fatalf("per-domain usage rows must not render:\n%s", out)
	}

	_, errb, code = run(t, d, nil, "--api-url", f.srv.URL, "--api-key", "k", "-o", "json", "org", "limits")
	if code != cerr.ExitUsage || !has(errb, "organization") {
		t.Fatalf("no org selected must be a usage error: %d %s", code, errb)
	}
}

// The JSON/YAML output is the wire body: an int64 limit past 2^53 stays exact.
func TestOrgLimitsPreservesLargeIntegers(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	large := int64(9007199254740993) // 2^53 + 1: float64 rounds it to ...992
	fixture := limitsFixture()
	fixture["storage"] = gen.StorageLimits{
		Images:    gen.UsageLimit{Usage: 5, Limit: &large},
		Snapshots: gen.UsageLimit{Usage: 7},
	}
	f.on("GET", "/orgs/"+testOrgA+"/limits", 200, fixture)
	f.on("GET", "/api-keys/current", 401, map[string]any{"code": "UNAUTHORIZED", "message": "unauthorized", "source": "DAYTONA_API"})
	for format, want := range map[string]string{"json": `"limit": 9007199254740993`, "yaml": "limit: 9007199254740993"} {
		out, errb, code := run(t, d, nil, "--api-url", f.srv.URL, "--api-key", "k", "-o", format, "--org", testOrgA, "org", "limits")
		if code != 0 {
			t.Fatalf("%s: %d %s", format, code, errb)
		}
		if !has(out, want) {
			t.Fatalf("%s output rounded the limit:\n%s", format, out)
		}
	}
}

// An API key's limits read carries no `members` group: the table renders
// every other limit and no Members row.
func TestOrgLimitsWithoutMembers(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	body := limitsFixture()
	delete(body, "members")
	f.on("GET", "/orgs/"+testOrgA+"/limits", 200, body)

	out, errb, code := run(t, d, nil, "--api-url", f.srv.URL, "--api-key", "k", "-o", "table", "org", "limits", "--org", testOrgA)

	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	if !has(out, "Concurrent sandboxes") || has(out, "Members") {
		t.Fatalf("table must render the limits without a Members row:\n%s", out)
	}
}

func currentKeyFixture() map[string]any {
	return map[string]any{
		"id":                  "33333333-3333-3333-3333-333333333333",
		"project_id":          testProjP1,
		"name":                "ci",
		"created_at":          "2026-10-23T10:00:00Z",
		"project_permissions": []string{"read:limits"},
		"sync_to_v0":          false,
		"context":             map[string]any{"org_id": testOrgA, "org_name": "Acme", "project_name": "default"},
	}
}

// A project API key with no organization named reads its own organization,
// learned from GET /api-keys/current; naming another organization is refused
// before any limits request.
func TestOrgLimitsWithProjectKeyUsesItsOwnOrg(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	f.on("GET", "/api-keys/current", 200, currentKeyFixture())
	body := limitsFixture()
	delete(body, "members")
	f.on("GET", "/orgs/"+testOrgA+"/limits", 200, body)

	out, errb, code := run(t, d, nil, "--api-url", f.srv.URL, "--api-key", "dtn_k", "-o", "table", "org", "limits")
	if code != 0 || !has(out, "Concurrent sandboxes") {
		t.Fatalf("own-org read: %d %s\n%s", code, errb, out)
	}

	for _, name := range []string{"Acme", "acme", " ACME "} {
		_, errb, code = run(t, d, nil, "--api-url", f.srv.URL, "--api-key", "dtn_k", "-o", "json", "org", "limits", name)
		if code != 0 {
			t.Fatalf("own org named %q: %d %s", name, code, errb)
		}
	}

	_, errb, code = run(t, d, nil, "--api-url", f.srv.URL, "--api-key", "dtn_k", "-o", "json", "org", "limits", "Other")
	if code != cerr.ExitUsage || !has(errb, "only its own organization (Acme)") {
		t.Fatalf("another org by name must be a usage error: %d %s", code, errb)
	}
}

// The organization a saved profile selected is not a reference named on this
// call: a project key, explicit or saved with `login --token`, still reads its
// own organization.
func TestOrgLimitsWithProjectKeyIgnoresTheSavedOrg(t *testing.T) {
	for _, tc := range []struct {
		name  string
		token string
		args  []string
	}{
		{"explicit key", "", []string{"--api-key", "dtn_k"}},
		{"saved token", "dtn_saved", nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := newFakeAPI(t)
			d := cfgDir(t)
			st, _ := config.Open(d)
			if err := st.Update(func(c *config.Config, cr *config.Credentials) (bool, bool, error) {
				c.Upsert(config.Profile{Name: "default", APIURL: f.srv.URL, Credential: "token", OrgID: testOrgP})
				c.ActiveProfile = "default"
				cr.Profiles["default"] = config.ProfileCredentials{Token: tc.token}
				return true, true, nil
			}); err != nil {
				t.Fatal(err)
			}
			f.on("GET", "/api-keys/current", 200, currentKeyFixture())
			body := limitsFixture()
			delete(body, "members")
			f.on("GET", "/orgs/"+testOrgA+"/limits", 200, body)

			out, errb, code := run(t, d, nil, append(tc.args, "-o", "table", "org", "limits")...)

			if code != 0 || !has(out, "Concurrent sandboxes") {
				t.Fatalf("own-org read: %d %s\n%s", code, errb, out)
			}
		})
	}
}

// When the response omits storage, the
// table leaves those rows out instead of printing "0 / unlimited", and the
// JSON output is the body as received, with no synthesized fields.
func TestOrgLimitsOmitsUnreportedGroups(t *testing.T) {
	// A plain server, not newFakeAPI: the incomplete body does not match the
	// current contract, which newFakeAPI enforces on every response.
	d := cfgDir(t)
	old := limitsFixture()
	delete(old, "storage")
	body, err := json.Marshal(old)
	if err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.URL.Path != "/orgs/"+testOrgA+"/limits" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(body)
	}))
	t.Cleanup(srv.Close)

	out, errb, code := run(t, d, nil, "--api-url", srv.URL, "--api-key", "k", "-o", "table", "org", "limits", "--org", testOrgA)
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	for _, absent := range []string{"Images", "Snapshots"} {
		if has(out, absent) {
			t.Fatalf("an unreported %q row must be omitted:\n%s", absent, out)
		}
	}
	if !has(out, "Concurrent sandboxes") || !has(out, "4 / 10") {
		t.Fatalf("reported rows must still render:\n%s", out)
	}

	out, errb, code = run(t, d, nil, "--api-url", srv.URL, "--api-key", "k", "-o", "json", "org", "limits", "--org", testOrgA)
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	var got map[string]any
	if err := json.Unmarshal([]byte(out), &got); err != nil {
		t.Fatal(err)
	}
	if _, ok := got["storage"]; ok {
		t.Fatalf("JSON output must not invent a storage group: %s", out)
	}
}

// A 429 with LIMIT_EXCEEDED exits 6 with a hint naming the organization limit,
// rather than a rate-limit retry hint or a generic failure.
func TestLimitExceededIsClassifiedByBodyCode(t *testing.T) {
	f := newFakeAPI(t)
	f.handle("POST", "/sandboxes", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(429)
		_, _ = w.Write([]byte(`{"code":"LIMIT_EXCEEDED","message":"organization limit reached: 10 of 10 (max_concurrent_sandboxes)","source":"DAYTONA_API","limit":"max_concurrent_sandboxes","current":10,"max":10}`))
	})
	f.on("GET", "/regions", 200, map[string]any{"regions": []any{}, "default_region_id": nil})
	_, errb, code := run(t, cfgDir(t), nil, "sandbox", "create", "--image", "python:3.12", "--no-wait", "-o", "json", "--api-url", f.srv.URL, "--api-key", "dtn_k", "--project", testProjP1)
	if code != cerr.ExitConflict {
		t.Fatalf("LIMIT_EXCEEDED must exit %d, got %d: %s", cerr.ExitConflict, code, errb)
	}
	if !has(errb, "max_concurrent_sandboxes") || !has(errb, "10 of 10") || !has(errb, "org limits") || has(errb, "retry shortly") {
		t.Fatalf("hint must name the limit and point at `org limits`: %s", errb)
	}

	// An overloaded response remains a retryable failure.
	f2 := newFakeAPI(t)
	f2.handle("POST", "/sandboxes", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Retry-After", "2")
		w.WriteHeader(429)
		_, _ = w.Write([]byte(`{"code":"OVERLOADED","message":"overloaded","source":"DAYTONA_API"}`))
	})
	f2.on("GET", "/regions", 200, map[string]any{"regions": []any{}, "default_region_id": nil})
	_, errb, code = run(t, cfgDir(t), nil, "sandbox", "create", "--image", "python:3.12", "--no-wait", "-o", "json", "--api-url", f2.srv.URL, "--api-key", "dtn_k", "--project", testProjP1)
	if code != cerr.ExitFailure || !has(errb, "retry shortly") {
		t.Fatalf("overloaded 429 stays a retryable failure: %d %s", code, errb)
	}
}

// When the response omits per-sandbox maximums, the table renders without
// those rows instead of a zero.
func TestOrgLimitsWithoutSandboxResources(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	body := limitsFixture()
	delete(body, "sandbox_resources")
	f.on("GET", "/orgs/"+testOrgA+"/limits", 200, body)

	out, errb, code := run(t, d, nil, "--api-url", f.srv.URL, "--api-key", "k", "-o", "table", "org", "limits", "--org", testOrgA)
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	if !has(out, "Concurrent sandboxes") || has(out, "per sandbox") {
		t.Fatalf("table must render without the per-sandbox rows:\n%s", out)
	}
}
