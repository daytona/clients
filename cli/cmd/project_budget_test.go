// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"testing"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"

	"github.com/daytona/clients/cli/internal/cerr"
)

func projectBudgetFixture(_ string, limit int64, exceeded bool) map[string]any {
	b := map[string]any{
		"monthly_limit_cents": limit,
		"period_start_date":   "2026-10-01",
		"spent_cents":         41237,
		"evaluated_at":        "2026-10-07T09:41:00Z",
		"exceeded_at":         nil,
		"stop_at":             nil,
		"created_at":          "2026-10-01T00:00:00Z",
		"updated_at":          "2026-10-01T00:00:00Z",
	}
	if exceeded {
		b["exceeded_at"] = "2026-10-07T09:41:00Z"
		b["stop_at"] = "2026-10-07T10:41:00Z"
	}
	return b
}

// runBudgetOIDC runs the CLI as a logged-in user: the budget routes admit
// OIDC user JWTs only, so the success paths run on an `oidcProfile` session
// (access token "jwt") instead of runMgmt's `--api-key`.
func runBudgetOIDC(t *testing.T, f *fakeAPI, d string, args ...string) (string, string, int) {
	t.Helper()
	return run(t, d, nil, append([]string{"--api-url", f.srv.URL, "-o", "json"}, args...)...)
}

// assertBudgetAuthJWT fails unless every budget request carried the session's
// user JWT as its bearer — never an API key.
func assertBudgetAuthJWT(t *testing.T, f *fakeAPI, path string) {
	t.Helper()
	seen := 0
	for _, r := range f.requests {
		if r.Path != path {
			continue
		}
		seen++
		if r.Auth != "Bearer jwt" {
			t.Fatalf("%s %s: Authorization %q, want the user JWT %q", r.Method, r.Path, r.Auth, "Bearer jwt")
		}
	}
	if seen == 0 {
		t.Fatalf("no request to %s", path)
	}
}

// `project budget get|set|remove` call GET/PUT/DELETE /projects/{id}/budget
// for the selected project or the named one, and render the budget.
func TestProjectBudgetCommands(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	path := "/projects/" + testProjA1 + "/budget"
	f.on("GET", "/orgs/"+testOrgA+"/projects", 200, projectsFixture(testOrgA, "default", "lab"))
	f.on("GET", path, 200, projectBudgetFixture(testProjA1, 50000, false))
	f.on("PUT", path, 200, projectBudgetFixture(testProjA1, 49999, false))
	f.handle("DELETE", path, func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) })

	// get, defaulting to --project. `-o json` prints the API's JSON, so the
	// fixture's null exceeded_at and stop_at stay null.
	out, errb, code := runBudgetOIDC(t, f, d, "--org", testOrgA, "--project", testProjA1, "project", "budget", "get")
	if code != 0 {
		t.Fatalf("get: %d %s", code, errb)
	}
	got := mustJSON(t, out)
	if got["monthly_limit_cents"].(float64) != 50000 || got["spent_cents"].(float64) != 41237 {
		t.Fatalf("get json: %s", out)
	}
	// The API's representation: its nulls stay null.
	for _, k := range []string{"exceeded_at", "stop_at"} {
		if v, ok := got[k]; !ok || v != nil {
			t.Fatalf("get json: %s must stay null as the API sent it: %s", k, out)
		}
	}

	// get by name, as a table.
	out, errb, code = run(t, d, nil, "--api-url", f.srv.URL, "-o", "table", "--org", testOrgA, "project", "budget", "get", "default")
	if code != 0 {
		t.Fatalf("get table: %d %s", code, errb)
	}
	for _, want := range []string{"$500.00", "$412.37", "2026-10-01 (UTC)", "not held"} {
		if !has(out, want) {
			t.Fatalf("table lacks %q:\n%s", want, out)
		}
	}

	// set sends whole cents, computed without float rounding.
	out, errb, code = runBudgetOIDC(t, f, d, "--org", testOrgA, "project", "budget", "set", testProjA1, "--monthly-usd", "499.99")
	if code != 0 || mustJSON(t, out)["monthly_limit_cents"].(float64) != 49999 {
		t.Fatalf("set: %d %s %s", code, out, errb)
	}
	put := f.requests[len(f.requests)-1]
	if put.Method != http.MethodPut || put.Path != path || put.Body["monthly_limit_cents"] != float64(49999) || len(put.Body) != 1 {
		t.Fatalf("put request: %+v", put)
	}

	// set validates the amount before any request.
	before := len(f.requests)
	_, errb, code = runBudgetOIDC(t, f, d, "--org", testOrgA, "project", "budget", "set", testProjA1, "--monthly-usd", "0.99")
	if code != cerr.ExitUsage || !has(errb, "at least 1.00") || len(f.requests) != before {
		t.Fatalf("set below minimum: %d %s", code, errb)
	}
	_, errb, code = runBudgetOIDC(t, f, d, "--org", testOrgA, "project", "budget", "set", testProjA1)
	if code != cerr.ExitUsage || !has(errb, "--monthly-usd is required") || len(f.requests) != before {
		t.Fatalf("set without amount: %d %s", code, errb)
	}

	// remove is a guarded verb: --yes off-TTY, refused before any request.
	_, errb, code = runBudgetOIDC(t, f, d, "--org", testOrgA, "project", "budget", "remove", testProjA1)
	if code != cerr.ExitUsage || !has(errb, "--yes") || len(f.requests) != before {
		t.Fatalf("remove guard: %d %s", code, errb)
	}
	out, errb, code = runBudgetOIDC(t, f, d, "--org", testOrgA, "--project", testProjA1, "project", "budget", "remove", "--yes")
	if code != 0 || !isAck(t, out, "project_id", testProjA1, "removed") {
		t.Fatalf("remove: %d %s %s", code, out, errb)
	}
	if countRequests(f.requests, http.MethodDelete, path) != 1 {
		t.Fatalf("delete not issued once: %+v", f.requests)
	}
	assertBudgetAuthJWT(t, f, path)
}

// A project without a budget answers 200 with null fields: `get` succeeds
// and shows the budget as not set.
func TestProjectBudgetGetWithoutBudget(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	f.on("GET", "/projects/"+testProjA1+"/budget", 200, map[string]any{"monthly_limit_cents": nil, "period_start_date": "2026-10-01", "created_at": nil, "updated_at": nil})
	out, errb, code := run(t, d, nil, "--api-url", f.srv.URL, "-o", "table", "--org", testOrgA, "--project", testProjA1, "project", "budget", "get")
	if code != 0 || !has(out, "not set") {
		t.Fatalf("no budget: %d %s %s", code, out, errb)
	}
	assertBudgetAuthJWT(t, f, "/projects/"+testProjA1+"/budget")
}

// A 404 on the budget read means the project was not found; it must not be
// reported as a missing budget.
func TestProjectBudgetGetProjectNotFound(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	f.on("GET", "/projects/"+testProjA1+"/budget", 404, map[string]any{"code": "NOT_FOUND", "message": "not found", "source": "DAYTONA_API"})
	_, errb, code := runBudgetOIDC(t, f, d, "--org", testOrgA, "--project", testProjA1, "project", "budget", "get")
	if code != cerr.ExitNotFound || has(errb, "no monthly budget") {
		t.Fatalf("project not found: %d %s", code, errb)
	}
}

// On DELETE a 404 means the project has no budget, and the hint says so.
func TestProjectBudgetRemoveWithoutBudget(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	f.on("DELETE", "/projects/"+testProjA1+"/budget", 404, map[string]any{"code": "NOT_FOUND", "message": "not found", "source": "DAYTONA_API"})
	_, errb, code := runBudgetOIDC(t, f, d, "--org", testOrgA, "--project", testProjA1, "project", "budget", "remove", "--yes")
	if code != cerr.ExitNotFound || !has(errb, "no monthly budget") {
		t.Fatalf("no budget: %d %s", code, errb)
	}
}

// The exhausted state renders the hold and the stop time.
func TestProjectBudgetGetExceeded(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	f.on("GET", "/projects/"+testProjA1+"/budget", 200, projectBudgetFixture(testProjA1, 100000000000, true))
	out, errb, code := run(t, d, nil, "--api-url", f.srv.URL, "-o", "table", "--org", testOrgA, "--project", testProjA1, "project", "budget", "get")
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	assertBudgetAuthJWT(t, f, "/projects/"+testProjA1+"/budget")
	for _, want := range []string{"$1,000,000,000.00", "held (over budget) since 2026-10-07 09:41 UTC", "2026-10-07 10:41 UTC"} {
		if !has(out, want) {
			t.Fatalf("table lacks %q:\n%s", want, out)
		}
	}
}

// The Status row reports the hold from exceeded_at rather than inferring it
// from the spend amount. A held
// budget whose grace period would outlast the month has stop_at null and
// prints no end time.
func TestProjectBudgetKVStatus(t *testing.T) {
	ts := func(s string) *time.Time {
		v, err := time.Parse(time.RFC3339, s)
		if err != nil {
			t.Fatal(err)
		}
		return &v
	}
	spent := int64(60000)
	limit := int64(50000)
	cases := []struct {
		name   string
		b      gen.ProjectBudget
		status string
		end    string
	}{
		{"held", gen.ProjectBudget{MonthlyLimitCents: &limit, SpentCents: &spent, ExceededAt: ts("2026-10-07T09:41:00Z"), StopAt: ts("2026-10-07T10:41:00Z")},
			"held (over budget) since 2026-10-07 09:41 UTC; new sandboxes can't start", "2026-10-07 10:41 UTC"},
		{"held, no stop this month", gen.ProjectBudget{MonthlyLimitCents: &limit, SpentCents: &spent, ExceededAt: ts("2026-10-31T23:30:00Z")},
			"held (over budget) since 2026-10-31 23:30 UTC; new sandboxes can't start", "none this month (the hold lifts at 00:00 UTC on the 1st first)"},
		{"not calculated", gen.ProjectBudget{MonthlyLimitCents: &limit}, "not calculated yet", "-"},
		// Spend alone does not indicate a hold without exceeded_at.
		{"spend exceeds limit without a hold", gen.ProjectBudget{MonthlyLimitCents: &limit, SpentCents: &spent}, "not held", "-"},
	}
	for _, tc := range cases {
		kv := map[string]string{}
		for _, row := range projectBudgetKV(&tc.b) {
			kv[row[0]] = row[1]
		}
		if kv["Status"] != tc.status || kv["Running sandboxes end at"] != tc.end {
			t.Errorf("%s: status %q, end %q; want %q, %q", tc.name, kv["Status"], kv["Running sandboxes end at"], tc.status, tc.end)
		}
		if has(kv["Status"], "under budget") {
			t.Errorf("%s: status claims under budget", tc.name)
		}
	}
}

// PROJECT_BUDGET_EXCEEDED keeps the 402 default exit code (1), like
// ORG_SUSPENDED, and points at `project budget get` and the login session
// that user-JWT route needs.
func TestProjectBudgetExceededHint(t *testing.T) {
	f := newFakeAPI(t)
	f.handle("POST", "/sandboxes", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(402)
		_, _ = w.Write([]byte(`{"code":"PROJECT_BUDGET_EXCEEDED","message":"project reached its monthly budget","source":"DAYTONA_API"}`))
	})
	f.on("GET", "/regions", 200, map[string]any{"regions": []any{}, "default_region_id": nil})
	_, errb, code := run(t, cfgDir(t), nil, "sandbox", "create", "--image", "python:3.12", "--no-wait", "-o", "json", "--api-url", f.srv.URL, "--api-key", "dtn_k", "--project", testProjP1)
	if code != cerr.ExitFailure {
		t.Fatalf("PROJECT_BUDGET_EXCEEDED must exit %d, got %d: %s", cerr.ExitFailure, code, errb)
	}
	if !has(errb, "daytona project budget get") || !has(errb, "daytona login") {
		t.Fatalf("hint must point at `project budget get` and `daytona login`: %s", errb)
	}
}

func TestParseUSDCents(t *testing.T) {
	cases := []struct {
		in   string
		want int64
		err  string
	}{
		{"1", 100, ""},
		{"1.00", 100, ""},
		{"500", 50000, ""},
		{"499.99", 49999, ""},
		{"12.5", 1250, ""},
		{"0.29", 0, "at least 1.00"},
		{"$1250.50", 125050, ""},
		{" 007.10 ", 710, ""},
		{"1000000000", 100000000000, ""},
		{"0.1", 0, "at least 1.00"},
		{"0.99", 0, "at least 1.00"},
		{"0", 0, "at least 1.00"},
		{"1.001", 0, "at most two decimals"},
		{"1.", 0, "at most two decimals"},
		{".50", 0, "at most two decimals"},
		{"-5", 0, "at most two decimals"},
		{"+5", 0, "at most two decimals"},
		{"1e3", 0, "at most two decimals"},
		{"1,000", 0, "at most two decimals"},
		{"abc", 0, "at most two decimals"},
		{"", 0, "at most two decimals"},
		{"NaN", 0, "at most two decimals"},
		{"1234567890123456", 0, "too large"},
	}
	for _, tc := range cases {
		got, err := parseUSDCents(tc.in)
		if tc.err != "" {
			if err == nil || !has(err.Error(), tc.err) || cerr.ExitCode(err) != cerr.ExitUsage {
				t.Errorf("%q: err %v, want usage error containing %q", tc.in, err, tc.err)
			}
			continue
		}
		if err != nil || got != tc.want {
			t.Errorf("%q: got %d, %v; want %d", tc.in, got, err, tc.want)
		}
	}
}

func TestFormatUSDCents(t *testing.T) {
	for in, want := range map[int64]string{0: "$0.00", 5: "$0.05", 100: "$1.00", 41237: "$412.37", 123456789: "$1,234,567.89", -250: "-$2.50"} {
		if got := formatUSDCents(in); got != want {
			t.Errorf("%d: got %q want %q", in, got, want)
		}
	}
}
