// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
)

// runReported runs the CLI like main does: the error goes through
// reportError, so stderr is what a script sees.
func runReported(t *testing.T, cfg string, env map[string]string, args ...string) (string, string, int) {
	t.Helper()
	for _, key := range []string{"DAYTONA_API_URL", "DAYTONA_V2_API_URL", "DAYTONA_API_KEY", "DAYTONA_PROFILE", "DAYTONA_ORG", "DAYTONA_PROJECT", "DAYTONA_REGION", "DAYTONA_OIDC_CLIENT_ID", "DAYTONA_YES"} {
		t.Setenv(key, "")
	}
	for k, v := range env {
		t.Setenv(k, v)
	}
	t.Setenv("DAYTONA_CONFIG_DIR", cfg)
	t.Setenv("CI", "1")
	var out, errb bytes.Buffer
	root := NewRoot(&out, &errb)
	root.SetArgs(args)
	err := root.Execute()
	if err == nil {
		return out.String(), errb.String(), 0
	}
	reportError(root, &out, &errb, err)
	return out.String(), errb.String(), cerr.ExitCode(err)
}

func errorObject(t *testing.T, stderr string) map[string]any {
	t.Helper()
	lines := strings.Split(strings.TrimSpace(stderr), "\n")
	require.Len(t, lines, 1, "one JSON object on stderr: %q", stderr)
	var doc map[string]map[string]any
	require.NoError(t, json.Unmarshal([]byte(lines[0]), &doc), stderr)
	require.Contains(t, doc, "error")
	return doc["error"]
}

// Every wrong arity, unknown command and unknown flag is a usage error.
func TestUsageErrorsExitTwo(t *testing.T) {
	for _, args := range [][]string{
		{"not-a-command"},
		{"sandbox", "not-a-command"},
		{"sandbox", "port", "nope"},
		{"config", "get"},
		{"config", "get", "confirm", "extra"},
		{"sandbox", "get"},
		{"sandbox", "get", "a", "b"},
		{"org", "list", "--no-such-flag"},
		{"image", "import", "img", "--ref", "python:3.12"}, // the alias is gone
		{"invite", "show", "dtni_x"},                       // renamed to `invite get`
		{"sandbox", "preview-url", "sbx", "8080"},          // moved under `sandbox port`
	} {
		_, stderr, code := runReported(t, cfgDir(t), nil, args...)
		require.Equal(t, cerr.ExitUsage, code, "%v: %s", args, stderr)
		require.Equal(t, float64(cerr.ExitUsage), errorObject(t, stderr)["exit_code"], args)
	}
	// A bare group prints its help and succeeds.
	stdout, _, code := runReported(t, cfgDir(t), nil, "sandbox")
	require.Equal(t, 0, code)
	require.Contains(t, stdout, "Available Commands")
}

// --file, --no-wait and --timeout exist only on the commands that use them.
func TestRequestAndWaitFlagsAreScoped(t *testing.T) {
	for _, args := range [][]string{
		{"config", "get", "confirm", "--file", "/does/not/exist"},
		{"config", "path", "--no-wait"},
		{"config", "path", "--timeout", "1s"},
		{"org", "create", "acme", "-f", "body.json"},
		{"image", "capture", "sbx", "--file", "body.json"},
		{"sandbox", "fork", "sbx", "--file", "body.json"},
		{"volume", "delete", "vol", "--no-wait"},
		{"secret", "list", "--timeout", "1s"},
	} {
		_, stderr, code := runReported(t, cfgDir(t), nil, args...)
		require.Equal(t, cerr.ExitUsage, code, "%v: %s", args, stderr)
		require.Contains(t, errorObject(t, stderr)["message"], "unknown", args)
	}
}

// Off a terminal the default output is JSON; --output wins; at a terminal the
// default is a table.
func TestDefaultOutputFollowsTheTerminal(t *testing.T) {
	d := cfgDir(t)
	stdout, _, code := runReported(t, d, nil, "config", "get", "confirm")
	require.Equal(t, 0, code)
	require.Equal(t, map[string]any{"confirm": true}, mustJSON(t, stdout))

	stdout, _, code = runReported(t, d, nil, "config", "get", "confirm", "-o", "table")
	require.Equal(t, 0, code)
	require.Equal(t, "true", strings.TrimSpace(stdout))

	tty := true
	app.ForceTTY = &tty
	t.Cleanup(func() { app.ForceTTY = nil })
	stdout, _, code = runReported(t, d, nil, "config", "get", "confirm")
	require.Equal(t, 0, code)
	require.Equal(t, "true", strings.TrimSpace(stdout))
}

// In JSON mode an error is one stderr object carrying the server's code,
// source and trace id; at a terminal (or with -o table) it stays prose.
func TestErrorsInJSONModeAreOneStderrObject(t *testing.T) {
	f := newFakeAPI(t)
	f.handle(http.MethodGet, "/snapshots/snap_1", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("x-daytona-trace-id", "trace-123")
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"code":"SNAPSHOT_NOT_FOUND","message":"snapshot snap_1 not found","source":"DAYTONA_API"}`))
	})
	stdout, stderr, code := runReported(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "k", "snapshot", "get", "snap_1")
	require.Equal(t, cerr.ExitNotFound, code)
	require.Empty(t, stdout)
	obj := errorObject(t, stderr)
	require.Equal(t, float64(cerr.ExitNotFound), obj["exit_code"])
	require.Equal(t, "SNAPSHOT_NOT_FOUND", obj["code"])
	require.Equal(t, "DAYTONA_API", obj["source"])
	require.Equal(t, "trace-123", obj["trace_id"])
	require.Contains(t, obj["message"], "snapshot snap_1 not found")
	require.NotEmpty(t, obj["hint"])

	_, stderr, code = runReported(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "k", "snapshot", "get", "snap_1", "-o", "table")
	require.Equal(t, cerr.ExitNotFound, code)
	require.True(t, strings.HasPrefix(stderr, "error: "), stderr)

	// YAML output is machine-readable too.
	_, stderr, code = runReported(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "k", "snapshot", "get", "snap_1", "-o", "yaml")
	require.Equal(t, cerr.ExitNotFound, code)
	require.Equal(t, "SNAPSHOT_NOT_FOUND", errorObject(t, stderr)["code"])

	// An unknown --output value is reported in the default for this stdout.
	_, stderr, code = runReported(t, cfgDir(t), nil, "config", "path", "-o", "bogus")
	require.Equal(t, cerr.ExitUsage, code)
	require.Equal(t, float64(cerr.ExitUsage), errorObject(t, stderr)["exit_code"])

	// A local error has source CLI and no server fields.
	_, stderr, code = runReported(t, cfgDir(t), nil, "whoami", "--profile", "ghost")
	require.Equal(t, cerr.ExitNotFound, code)
	obj = errorObject(t, stderr)
	require.Equal(t, "CLI", obj["source"])
	require.NotContains(t, obj, "code")
	require.NotContains(t, obj, "trace_id")
}

func TestSecretSetFromEmptyFilePathIsAUsageError(t *testing.T) {
	f := newFakeAPI(t)
	for _, path := range []string{"", "  "} {
		_, stderr, code := runMgmt(t, f, cfgDir(t), "--project", "proj-1", "secret", "set", "API_KEY", "--from-file", path)
		require.Equal(t, cerr.ExitUsage, code, stderr)
		require.Contains(t, stderr, "--from-file is empty")
	}
	require.Empty(t, f.requests, "refused before any request")
}

func TestBillingDeletesNeedConsent(t *testing.T) {
	for _, setting := range []string{"automatic-top-up", "balance-notification"} {
		f := newFakeAPI(t)
		_, stderr, code := runMgmt(t, f, cfgDir(t), "--org", testOrgP, "billing", setting, "delete")
		require.Equal(t, cerr.ExitUsage, code, stderr)
		require.Contains(t, stderr, "--yes")
		require.Empty(t, f.requests)
	}
}

func TestRepeatedRoleAndPermissionFlagsAccumulate(t *testing.T) {
	f := newFakeAPI(t)
	f.on(http.MethodPost, "/orgs/"+testOrgP+"/service-accounts", 201, map[string]any{
		"id": "66666666-6666-6666-6666-666666666666", "name": "ci", "org_id": testOrgP, "roles": []string{"developer", "auditor", "billing"},
		"org_permissions": []string{}, "live_key_count": 0, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
	})
	_, stderr, code := runMgmt(t, f, cfgDir(t), "--org", testOrgP, "service-account", "create", "ci", "--role", "developer", "--role", "auditor,billing")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, []any{"developer", "auditor", "billing"}, findRequest(t, f, http.MethodPost, "/orgs/"+testOrgP+"/service-accounts").Body["roles"])
}

func TestListLimitIsValidatedBeforeAnyRequest(t *testing.T) {
	f := newFakeAPI(t)
	for _, args := range [][]string{
		{"secret", "list", "--limit", "0"},
		{"registry", "list", "--limit", "-1"},
		{"audit", "list", "--limit", "201"},
		{"org", "list", "--limit", "x"},
		{"sandbox", "list", "--limit", "0"},
	} {
		_, stderr, code := runMgmt(t, f, cfgDir(t), append([]string{"--org", testOrgP, "--project", "proj-1"}, args...)...)
		require.Equal(t, cerr.ExitUsage, code, "%v: %s", args, stderr)
	}
	require.Empty(t, f.requests)
}

// Management lists return one page like the other lists; --all follows every
// page and an empty result is always an array.
func TestManagementListsReturnOnePage(t *testing.T) {
	f := newFakeAPI(t)
	f.handle(http.MethodGet, "/orgs/"+testOrgP+"/projects", func(w http.ResponseWriter, r *http.Request) {
		page := projectsFixture(testOrgP, "default", "lab")
		if r.URL.Query().Get("cursor") == "" {
			page["projects"] = page["projects"].([]map[string]any)[:1]
			page["next_cursor"] = "page-2"
		} else {
			page["projects"] = page["projects"].([]map[string]any)[1:]
		}
		_ = json.NewEncoder(w).Encode(page)
	})
	stdout, stderr, code := runMgmt(t, f, cfgDir(t), "--org", testOrgP, "project", "list", "--limit", "1")
	require.Equal(t, 0, code, stderr)
	out := mustJSON(t, stdout)
	require.Len(t, out["items"], 1)
	require.Equal(t, "page-2", out["next_cursor"])
	require.Equal(t, "limit=1", f.requests[0].Query)

	stdout, stderr, code = runMgmt(t, f, cfgDir(t), "--org", testOrgP, "project", "list", "--cursor", "page-2")
	require.Equal(t, 0, code, stderr)
	require.Len(t, mustJSON(t, stdout)["items"], 1)

	stdout, stderr, code = runMgmt(t, f, cfgDir(t), "--org", testOrgP, "project", "list", "--all")
	require.Equal(t, 0, code, stderr)
	out = mustJSON(t, stdout)
	require.Len(t, out["items"], 2)
	require.NotContains(t, out, "next_cursor")

	empty := newFakeAPI(t)
	empty.on(http.MethodGet, "/orgs/"+testOrgP+"/members", 200, map[string]any{"members": []any{}})
	stdout, stderr, code = runMgmt(t, empty, cfgDir(t), "--org", testOrgP, "member", "list", "--all")
	require.Equal(t, 0, code, stderr)
	require.Contains(t, stdout, `"items": []`)
}

func TestImageLogsFollowJSONIsNDJSONAndYAMLIsRefused(t *testing.T) {
	f := newFakeAPI(t)
	_, stderr, code := run(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "k", "-o", "yaml", "image", "logs", "img", "--follow")
	require.Equal(t, cerr.ExitUsage, code, stderr)
	require.Empty(t, f.requests, "refused before any request")
}

// DAYTONA_REGION is not read: only --region selects a region.
func TestDaytonaRegionIsNotRead(t *testing.T) {
	f := newFakeAPI(t)
	f.on(http.MethodGet, "/sandboxes/search", 200, map[string]any{"sandboxes": []any{}, "total": 0, "total_relation": "eq"})
	f.on(http.MethodGet, "/regions", 200, regionsFixture())
	_, stderr, code := run(t, cfgDir(t), map[string]string{"DAYTONA_REGION": "region-b"}, "--api-url", f.srv.URL, "--api-key", "k", "-o", "json", "sandbox", "search")
	require.Equal(t, 0, code, stderr)
	for _, r := range f.requests {
		require.NotContains(t, r.Query, "region-b")
	}
	_, stderr, code = run(t, cfgDir(t), map[string]string{"DAYTONA_REGION": "region-b"}, "--api-url", f.srv.URL, "--api-key", "k", "-o", "json", "reservation", "create", "--dry-run")
	require.Equal(t, cerr.ExitUsage, code, stderr)
	require.Contains(t, stderr, "needs --region")
}

// A process's own exit status passes through; its error source is PROCESS
// so a script can tell it from a CLI failure with the same number.
func TestProcessExitStatusPassesThroughWithSourceProcess(t *testing.T) {
	err := exitError(tbgen.ExitResult{Reason: reasonPtr(tbgen.ReasonExited), ExitCode: int32Ptr(6)})
	require.Equal(t, 6, cerr.ExitCode(err))
	details := cerr.DetailsOf(err)
	require.Equal(t, cerr.SourceProcess, details.Source)

	var buf bytes.Buffer
	printErrorJSON(&buf, err)
	obj := errorObject(t, buf.String())
	require.Equal(t, float64(6), obj["exit_code"])
	require.Equal(t, "PROCESS", obj["source"])
}

func reasonPtr(r tbgen.Reason) *tbgen.Reason { return &r }

func int32Ptr(v int32) *int32 { return &v }
