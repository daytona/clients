// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/stretchr/testify/require"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/api"
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
		{"registry", "set", "ghcr.io"},                     // a required flag is missing
		{"login", "--token", "dtn_x", "--device"},          // mutually exclusive flags
		{"login", "--device", "--browser"},
		{"sandbox", "list", "--region="}, // an empty --region is refused everywhere
		{"sandbox", "search", "q", "--region", " "},
		{"image", "list", "--region="},
		{"sandbox", "create", "--region="},
	} {
		_, stderr, code := runReported(t, cfgDir(t), nil, args...)
		require.Equal(t, cerr.ExitUsage, code, "%v: %s", args, stderr)
		require.Equal(t, float64(cerr.ExitUsage), errorObject(t, stderr)["exit_code"], args)
	}
	// Removed and moved commands name their replacement.
	for args, want := range map[string]string{
		"invite show dtni_x":      "invite get",
		"sandbox preview-url s 1": "sandbox port preview-url",
		"image import img":        "image create",
	} {
		_, stderr, code := runReported(t, cfgDir(t), nil, strings.Fields(args)...)
		require.Equal(t, cerr.ExitUsage, code, args)
		require.Contains(t, errorObject(t, stderr)["hint"], want, args)
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

// A renamed flag is an unknown flag whose hint names the replacement; the
// old bucket-region spelling on `volume create` is not taken by another
// --region.
func TestRenamedFlagsNameTheirReplacement(t *testing.T) {
	cases := map[string][]string{
		"--bucket-region":        {"volume", "create", "v", "--type", "bucket", "--region", "us-east-1"},
		"--size-gib":             {"volume", "create", "v", "--size-gb", "10"},
		"--dry-run":              {"member", "remove", "u", "--preview"},
		"--cpu-max":              {"sandbox", "create", "--cores-max", "2"},
		"--memory-max":           {"sandbox", "create", "--mem-max", "2048"},
		"--on-lifecycle-timeout": {"sandbox", "update", "s", "--on-timeout", "delete"},
	}
	for want, args := range cases {
		f := newFakeAPI(t)
		_, stderr, code := runReported(t, cfgDir(t), nil, append([]string{"--api-url", f.srv.URL, "--api-key", "k"}, args...)...)
		require.Equal(t, cerr.ExitUsage, code, "%v: %s", args, stderr)
		require.Contains(t, errorObject(t, stderr)["hint"], want, args)
		require.Empty(t, f.requests)
	}
}

// --region exists only on the commands that place or list by region.
func TestRegionFlagIsScoped(t *testing.T) {
	for _, args := range [][]string{
		{"volume", "list", "--region", "r"},
		{"snapshot", "list", "--region", "r"},
		{"config", "path", "--region", "r"},
		{"sandbox", "get", "s", "--region", "r"},
	} {
		_, stderr, code := runReported(t, cfgDir(t), nil, args...)
		require.Equal(t, cerr.ExitUsage, code, "%v: %s", args, stderr)
		require.Contains(t, errorObject(t, stderr)["message"], "unknown flag: --region")
	}
	for _, path := range [][]string{
		{"sandbox", "create"}, {"sandbox", "create-from-snapshot"}, {"sandbox", "create-macos"},
		{"sandbox", "list"}, {"sandbox", "search"}, {"image", "list"}, {"image", "create"},
		{"image", "build"}, {"reservation", "create"}, {"cpu-type", "list"}, {"mac-model", "list"},
	} {
		cmd, _, err := NewRoot(io.Discard, io.Discard).Find(path)
		require.NoError(t, err)
		require.NotNil(t, cmd.Flags().Lookup("region"), path)
	}
}

// An unknown DAYTONA_PROFILE / --profile stops only the commands that act on
// the selected profile.
func TestUnknownProfileStopsOnlyProfileCommands(t *testing.T) {
	ghost := map[string]string{"DAYTONA_PROFILE": "ghost"}
	for _, args := range [][]string{
		{"profile", "list"}, {"config", "get", "confirm"}, {"config", "path"},
		{"help"}, {"help", "sandbox"}, {"docs"}, {"completion", "bash"},
		{"__complete", "sand"}, {"__complete", "sandbox", "get", ""},
	} {
		_, stderr, code := runReported(t, cfgDir(t), ghost, args...)
		require.Equal(t, 0, code, "%v: %s", args, stderr)
	}
	stdout, _, code := runReported(t, cfgDir(t), ghost, "__complete", "sand")
	require.Equal(t, 0, code)
	require.Contains(t, stdout, "sandbox")

	_, stderr, code := runReported(t, cfgDir(t), ghost, "whoami")
	require.Equal(t, cerr.ExitNotFound, code)
	require.Contains(t, errorObject(t, stderr)["hint"], "DAYTONA_PROFILE")

	// Only an explicit `login --profile NAME` creates a profile.
	d := cfgDir(t)
	_, stderr, code = runReported(t, d, ghost, "login", "--token", "dtn_fixture")
	require.Equal(t, cerr.ExitNotFound, code, stderr)
	_, stderr, code = runReported(t, d, ghost, "login", "--profile", "ghost", "--token", "dtn_fixture")
	require.Equal(t, 0, code, stderr)
	stdout, _, code = runReported(t, d, ghost, "whoami")
	require.Equal(t, 0, code)
	require.Equal(t, "ghost", mustJSON(t, stdout)["profile"])
}

func TestHelpForAnUnknownTopicIsAUsageError(t *testing.T) {
	for _, args := range [][]string{{"help", "nope"}, {"help", "sandbox", "nope"}} {
		_, stderr, code := runReported(t, cfgDir(t), nil, args...)
		require.Equal(t, cerr.ExitUsage, code, "%v: %s", args, stderr)
	}
	stdout, _, code := runReported(t, cfgDir(t), nil, "help", "sandbox", "get")
	require.Equal(t, 0, code)
	require.Contains(t, stdout, "Usage:")
}

// A server error that names no source is never attributed to the CLI.
func TestServerErrorsWithoutASourceAreNotTheCLIs(t *testing.T) {
	f := newFakeAPI(t)
	f.handle(http.MethodGet, "/snapshots/snap_1", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte("upstream failed"))
	})
	_, stderr, code := runReported(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "k", "snapshot", "get", "snap_1")
	require.Equal(t, cerr.ExitFailure, code)
	obj := errorObject(t, stderr)
	require.NotContains(t, obj, "source")
	require.Contains(t, obj["message"], "upstream failed")
}

func TestWithSourceWinsOverAWrappedServerSource(t *testing.T) {
	err := cerr.Wrap(6, &api.APIError{Status: 409, Code: "CONFLICT", Source: "DAYTONA_API"}, "conflict").WithSource(cerr.SourceProcess)
	d := cerr.DetailsOf(err)
	require.Equal(t, cerr.SourceProcess, d.Source)
	require.Equal(t, "CONFLICT", d.Code)
}

// A resolver that passes an unknown name through unchanged does not make it
// an ID.
func TestWhoamiTreatsAPassedThroughNameAsUnresolved(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/orgs", 404, `{"code":"NOT_FOUND","message":"not found","source":"DAYTONA_API"}`)
	stdout, stderr, code := runReported(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "k", "--org", "Acme", "whoami")
	require.Equal(t, 0, code, stderr)
	out := mustJSON(t, stdout)
	require.NotContains(t, out, "org_id")
	require.Equal(t, "Acme", out["org_ref"])
	require.Equal(t, false, out["context_resolved"])
}

func TestSecretSetRefusesAnEmptyValueFromAnySource(t *testing.T) {
	empty := filepath.Join(t.TempDir(), "empty.txt")
	require.NoError(t, os.WriteFile(empty, []byte("\n"), 0o600))
	f := newFakeAPI(t)
	for _, args := range [][]string{
		{"--value", ""},
		{"--from-file", empty},
	} {
		_, stderr, code := runMgmt(t, f, cfgDir(t), append([]string{"--project", "proj-1", "secret", "set", "API_KEY"}, args...)...)
		require.Equal(t, cerr.ExitUsage, code, "%v: %s", args, stderr)
		require.Contains(t, stderr, "the secret value is empty")
	}
	require.Empty(t, f.requests)
}

func TestCreateLimitsAreCheckedBeforeAnyRequest(t *testing.T) {
	body := filepath.Join(t.TempDir(), "create.json")
	require.NoError(t, os.WriteFile(body, []byte(`{"cores_max":0.75}`), 0o600))
	for _, args := range [][]string{
		{"sandbox", "create", "--cpu-max", "1.3"},
		{"sandbox", "create", "--cpu-max", "0"},
		{"sandbox", "create", "--file", body},
		{"sandbox", "create", "--idle-timeout", "30s"},
		{"sandbox", "update", "s", "--idle-timeout", "59s"},
	} {
		f := newFakeAPI(t)
		_, stderr, code := runReported(t, cfgDir(t), nil, append([]string{"--api-url", f.srv.URL, "--api-key", "k"}, args...)...)
		require.Equal(t, cerr.ExitUsage, code, "%v: %s", args, stderr)
		require.Empty(t, f.requests, args)
	}
}

// image create takes a registry source; a Dockerfile goes through image build.
func TestImageCreateFileRefusesDockerfile(t *testing.T) {
	body := filepath.Join(t.TempDir(), "image.json")
	require.NoError(t, os.WriteFile(body, []byte(`{"dockerfile":"FROM scratch","source":{"type":"registry","ref":"python:3.12"}}`), 0o600))
	_, _, err := runCLI(t, []string{"image", "create", "img", "--file", body}, nil)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.ErrorContains(t, err, "dockerfile")
}

// An explicit flag wins over --file even when it is empty.
func TestImageBuildExplicitEmptyFlagsDoNotFallBackToTheFile(t *testing.T) {
	dir := t.TempDir()
	body := filepath.Join(dir, "build.json")
	require.NoError(t, os.WriteFile(body, []byte(`{"context":"`+dir+`","dockerfile":"Dockerfile"}`), 0o600))
	for _, args := range [][]string{{"--context", ""}, {"--dockerfile", ""}} {
		_, _, err := runCLI(t, append([]string{"image", "build", "img", "--file", body}, args...), nil)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), args)
	}
}

// --file keys name request fields exactly, on every command: a key that
// differs only in case is an unknown field, also when a flag sets the field.
func TestFileKeysAreExactOnEveryCommand(t *testing.T) {
	dir := t.TempDir()
	for _, tc := range []struct {
		body string
		args []string
	}{
		{`{"Name":"x"}`, []string{"sandbox", "create", "--no-wait"}},
		{`{"Lifecycle_Policy":{"on_timeout":"delete"}}`, []string{"sandbox", "create", "--lifecycle-timeout", "1h", "--no-wait"}},
		{`{"lifecycle_policy":{"On_Timeout":"delete"}}`, []string{"sandbox", "create", "--no-wait"}},
		{`{"volumes":[{"volume":"v","Mount_Path":"/m"}]}`, []string{"sandbox", "create", "--no-wait"}},
		{`{"Name":"x"}`, []string{"sandbox", "create-from-snapshot", "snap", "--no-wait"}},
		{`{"Type":"disk"}`, []string{"volume", "create", "vol"}},
		{`{"bucket":{"Name":"b"}}`, []string{"volume", "create", "vol", "--type", "bucket"}},
		{`{"Name":"x"}`, []string{"snapshot", "create", "s", "--no-wait"}},
		{`{"Source":{"type":"registry","ref":"python:3.12"}}`, []string{"image", "create", "img", "--no-wait"}},
		{`{"source":{"Type":"registry","ref":"python:3.12"}}`, []string{"image", "create", "img", "--no-wait"}},
		{`{"source":{"type":"registry","ref":"x","extra":1}}`, []string{"image", "create", "img", "--no-wait"}},
		{`{"Labels":{"a":"b"}}`, []string{"sandbox", "update", "s"}},
		{`{"lifecycle_policy":{"Timeout_Seconds":60}}`, []string{"sandbox", "update", "s"}},
		{`{"Dockerfile":"Dockerfile","context":"."}`, []string{"image", "build", "img", "--no-wait"}},
		{`{"Model":"m"}`, []string{"reservation", "create", "--region", "r", "--yes"}},
		// The key check runs before the lifecycle check reads `persistent`.
		{`{"Persistent":true,"lifecycle_policy":{"on_timeout":"stop"}}`, []string{"sandbox", "create-macos", "--image", "m", "--no-wait", "--yes"}},
	} {
		path := filepath.Join(dir, "body.json")
		require.NoError(t, os.WriteFile(path, []byte(tc.body), 0o600))
		f := newFakeAPI(t)
		f.on(http.MethodGet, "/sandboxes/s", 200, sandboxDetailFixture("s", "linux", "running"))
		args := append([]string{"--api-url", f.srv.URL, "--api-key", "k"}, tc.args...)
		_, stderr, code := runReported(t, cfgDir(t), map[string]string{"DAYTONA_TEST_SKIP_CONTRACT": "1"}, append(args, "--file", path)...)
		require.Equal(t, cerr.ExitUsage, code, "%v %s: %s", tc.args, tc.body, stderr)
		require.Contains(t, errorObject(t, stderr)["message"], "unknown field", tc.body)
		for _, r := range f.requests {
			require.Equal(t, http.MethodGet, r.Method, "%v %s: nothing is sent", tc.args, tc.body)
		}
	}
}

// env and labels keys are the user's names: keys that differ only in case
// are distinct and sent as given.
func TestSandboxCreateFileMapKeysAreSentAsGiven(t *testing.T) {
	body := filepath.Join(t.TempDir(), "create.json")
	require.NoError(t, os.WriteFile(body, []byte(`{"env":{"PATH":"1","path":"2"},"labels":{"a":"1","A":"2"}}`), 0o600))
	_, _, err := runCLI(t, []string{"sandbox", "create", "--file", body, "--no-wait"}, []expectedRequest{{
		method:   http.MethodPost,
		path:     "/sandboxes",
		status:   202,
		bodyJSON: `{"env":{"PATH":"1","path":"2"},"labels":{"a":"1","A":"2"}}`,
		response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"11111111-1111-1111-1111-111111111111","name":"box","status":"pending","wait_cap_seconds":3}`,
	}})
	require.NoError(t, err)
}

func TestSandboxUpdateRefusesANonObjectLifecyclePolicyEvenWithFlags(t *testing.T) {
	body := filepath.Join(t.TempDir(), "update.json")
	require.NoError(t, os.WriteFile(body, []byte(`{"lifecycle_policy":"never"}`), 0o600))
	_, _, err := runCLI(t, []string{"sandbox", "update", "s", "--file", body, "--idle-timeout", "1m", "--on-idle-timeout", "delete"}, nil)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.ErrorContains(t, err, "must be an object")
}

// JSON output is the API's representation for lists and toolbox output too.
func TestJSONOutputIsTheAPIsRepresentation(t *testing.T) {
	t.Run("list items", func(t *testing.T) {
		f := newFakeAPI(t)
		f.handle(http.MethodGet, "/orgs", func(w http.ResponseWriter, _ *http.Request) {
			_, _ = w.Write([]byte(`{"organizations":[{"id":"11111111-1111-1111-1111-111111111111","name":"a","personal":true,"brand_new_field":"x"}]}`))
		})
		stdout, stderr, code := runReported(t, cfgDir(t), map[string]string{"DAYTONA_TEST_SKIP_CONTRACT": "1"}, "--api-url", f.srv.URL, "--api-key", "k", "org", "list")
		require.Equal(t, 0, code, stderr)
		item := mustJSON(t, stdout)["items"].([]any)[0].(map[string]any)
		require.Equal(t, "x", item["brand_new_field"])
		for _, absent := range []string{"created_at", "updated_at", "membership"} {
			require.NotContains(t, item, absent)
		}
	})
	t.Run("object", func(t *testing.T) {
		f := newFakeAPI(t)
		f.handle(http.MethodGet, "/orgs/11111111-1111-1111-1111-111111111111", func(w http.ResponseWriter, _ *http.Request) {
			_, _ = w.Write([]byte(`{"id":"11111111-1111-1111-1111-111111111111","name":"a","brand_new_field":{"n":12345678901234567890}}`))
		})
		stdout, stderr, code := runReported(t, cfgDir(t), map[string]string{"DAYTONA_TEST_SKIP_CONTRACT": "1"}, "--api-url", f.srv.URL, "--api-key", "k", "org", "get", "11111111-1111-1111-1111-111111111111")
		require.Equal(t, 0, code, stderr)
		require.Contains(t, stdout, "12345678901234567890", "numbers keep their precision")
		out := mustJSON(t, stdout)
		require.NotContains(t, out, "created_at")
		require.NotContains(t, out, "personal")
	})
	t.Run("toolbox", func(t *testing.T) {
		stdout, _, err := runToolboxCLI(t, []string{"sandbox", "process", "get", "sbx", "p1"}, []toolboxExpectedRequest{
			{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
			{method: http.MethodGet, path: "/test-tools/sbx/processes/p1", status: 200, auth: true, response: `{"id":"p1","kind":"exec","state":"terminal","stdin":"closed","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","signal":"SIGTERM","futureField":1}`},
		})
		require.NoError(t, err)
		out := requireJSONStdout(t, stdout)
		require.Equal(t, float64(1), out["future_field"], "unknown fields pass through, in snake_case")
		require.Equal(t, "TERM", out["signal"], "signal names are bare")
		for _, absent := range []string{"pid", "exit_code", "name"} {
			require.NotContains(t, out, absent)
		}
	})
}

// Region fan-out lists: a failure no region survived is that failure; some
// regions failing is a partial result (exit 7) that names each failure.
func TestFanOutListFailures(t *testing.T) {
	t.Run("the only target refuses the credential", func(t *testing.T) {
		f := newFakeAPI(t)
		f.on(http.MethodGet, "/regions", 200, map[string]any{"regions": []any{}, "default_region_id": nil})
		f.on(http.MethodGet, "/sandboxes", 401, `{"code":"UNAUTHORIZED","message":"unauthorized","source":"DAYTONA_API"}`)
		stdout, stderr, code := runReported(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "k", "sandbox", "list")
		require.Equal(t, cerr.ExitAuth, code, stderr)
		require.Empty(t, stdout)
		obj := errorObject(t, stderr)
		require.Equal(t, "UNAUTHORIZED", obj["code"])
		require.Equal(t, "DAYTONA_API", obj["source"])
	})
	t.Run("an unknown region", func(t *testing.T) {
		f := newFakeAPI(t)
		f.on(http.MethodGet, "/regions", 200, regionsFixture())
		stdout, stderr, code := runReported(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "k", "sandbox", "list", "--region", "nope")
		require.Equal(t, cerr.ExitUsage, code, stderr)
		require.Empty(t, stdout)
		for _, r := range f.requests {
			require.NotEqual(t, "/sandboxes", r.Path)
		}
	})
	t.Run("some regions fail", func(t *testing.T) {
		bad := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusForbidden)
			_, _ = w.Write([]byte(`{"code":"FORBIDDEN","message":"no access","source":"DAYTONA_API","trace_id":"tr-b"}`))
		}))
		defer bad.Close()
		f := newFakeAPI(t)
		f.on(http.MethodGet, "/regions", 200, `{"regions":[{"id":"region-a","name":"A","available":true,"parent_region_ids":[]},`+regionalAPIRow("region-b", bad.URL)+`],"default_region_id":null}`)
		f.on(http.MethodGet, "/sandboxes", 200, map[string]any{"sandboxes": []any{}})
		stdout, stderr, code := runReported(t, cfgDir(t), map[string]string{"DAYTONA_TEST_SKIP_CONTRACT": "1"}, "--api-url", f.srv.URL, "--api-key", "k", "sandbox", "list")
		require.Equal(t, cerr.ExitPartial, code, stderr)
		out := mustJSON(t, stdout)
		require.Equal(t, true, out["incomplete"])
		failure := out["errors"].([]any)[0].(map[string]any)
		require.Equal(t, "region-b", failure["region"])
		require.Equal(t, float64(403), failure["status"])
		require.Equal(t, "FORBIDDEN", failure["code"])
		require.Equal(t, "tr-b", failure["trace_id"])
		obj := errorObject(t, stderr)
		require.Equal(t, float64(cerr.ExitPartial), obj["exit_code"])
		require.NotContains(t, obj, "source", "a partial list is not the CLI's own failure")
		// Table output prints no `errors`: the message keeps status and code.
		require.Contains(t, obj["message"], "region-b: 403 no access (FORBIDDEN) [trace tr-b]")
	})
	t.Run("every region fails", func(t *testing.T) {
		bad := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusForbidden)
			_, _ = w.Write([]byte(`{"code":"FORBIDDEN","message":"no access","source":"DAYTONA_API"}`))
		}))
		defer bad.Close()
		f := newFakeAPI(t)
		f.on(http.MethodGet, "/regions", 200, `{"regions":[{"id":"region-a","name":"A","available":true,"parent_region_ids":[]},`+regionalAPIRow("region-b", bad.URL)+`],"default_region_id":null}`)
		f.on(http.MethodGet, "/sandboxes", 401, `{"code":"UNAUTHORIZED","message":"bad key","source":"DAYTONA_API","trace_id":"tr-a"}`)
		stdout, stderr, code := runReported(t, cfgDir(t), map[string]string{"DAYTONA_TEST_SKIP_CONTRACT": "1"}, "--api-url", f.srv.URL, "--api-key", "k", "sandbox", "list")
		require.Equal(t, cerr.ExitAuth, code, stderr)
		require.Empty(t, stdout)
		obj := errorObject(t, stderr)
		require.Equal(t, "UNAUTHORIZED", obj["code"], "the first region's attribution")
		require.Equal(t, "tr-a", obj["trace_id"])
		require.Contains(t, obj["message"], "region-a: 401 bad key (UNAUTHORIZED)")
		require.Contains(t, obj["message"], "region-b: 403 no access (FORBIDDEN)")
		require.NotEmpty(t, obj["hint"])
	})
}

// A --timeout that nothing would wait for is refused.
func TestTimeoutWithoutAWaitIsRefused(t *testing.T) {
	for _, args := range [][]string{
		{"sandbox", "create", "--no-wait", "--timeout", "5s"},
		{"sandbox", "stop", "s", "--no-wait", "--timeout", "5s"},
		{"image", "logs", "img", "--timeout", "5s"},
		{"sandbox", "process", "logs", "s", "p", "--timeout", "5s"},
		{"sandbox", "docker", "status", "s", "--timeout", "5s"},
	} {
		f := newFakeAPI(t)
		_, stderr, code := runReported(t, cfgDir(t), nil, append([]string{"--api-url", f.srv.URL, "--api-key", "k"}, args...)...)
		require.Equal(t, cerr.ExitUsage, code, "%v: %s", args, stderr)
		require.Empty(t, f.requests, args)
	}
}

// help, docs and completion need no config: they work with a malformed one.
func TestHelpDocsAndCompletionRunWithAMalformedConfig(t *testing.T) {
	d := cfgDir(t)
	require.NoError(t, os.WriteFile(filepath.Join(d, "config.yaml"), []byte("profiles: [\n"), 0o600))
	for _, args := range [][]string{{"help"}, {"help", "sandbox"}, {"docs"}, {"completion", "bash"}, {"__complete", "sand"}} {
		_, stderr, code := runReported(t, d, nil, args...)
		require.Equal(t, 0, code, "%v: %s", args, stderr)
	}
	_, stderr, code := runReported(t, d, nil, "whoami")
	require.Equal(t, cerr.ExitFailure, code, "a command that needs the config still fails")
	require.Contains(t, errorObject(t, stderr)["message"], "config.yaml", "and it fails on the config")
}

// Per-command flags with a value also work before the command name; --no-wait
// there is refused with a hint, never ignored.
func TestPerCommandFlagsBeforeTheCommandName(t *testing.T) {
	f := newFakeAPI(t)
	_, stderr, code := runReported(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "k", "--no-wait", "sandbox", "stop", "s")
	require.Equal(t, cerr.ExitUsage, code, stderr)
	require.Contains(t, errorObject(t, stderr)["hint"], "after the command name")
	_, stderr, code = runReported(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "k", "--region", "region-a", "org", "list")
	require.Equal(t, cerr.ExitUsage, code, stderr)
	require.Empty(t, f.requests)
}

// isAck reports whether out is exactly the acknowledgement
// {idField: id, action: true}.
func isAck(t *testing.T, out, idField, id, action string) bool {
	t.Helper()
	got := mustJSON(t, out)
	return len(got) == 2 && got[idField] == id && got[action] == true
}

// A region fan-out list prints each row's own JSON: the same id from two
// regions keeps the copy whose decoded value is the row that was kept.
func TestFanOutListPrintsEachRowsOwnJSON(t *testing.T) {
	regional := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"sandboxes":[{"id":"s1","name":"n","status":"running","region_id":"region-b","copy":"b"},{"id":"s2","name":"m","status":"running","region_id":"region-b","copy":"b2"}]}`))
	}))
	defer regional.Close()
	f := newFakeAPI(t)
	f.on(http.MethodGet, "/regions", 200, `{"regions":[{"id":"region-a","name":"A","available":true,"parent_region_ids":[]},`+regionalAPIRow("region-b", regional.URL)+`],"default_region_id":null}`)
	f.handle(http.MethodGet, "/sandboxes", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"sandboxes":[{"id":"s1","name":"n","status":"running","region_id":"region-a","copy":"a"}]}`))
	})
	stdout, stderr, code := runReported(t, cfgDir(t), map[string]string{"DAYTONA_TEST_SKIP_CONTRACT": "1"}, "--api-url", f.srv.URL, "--api-key", "k", "sandbox", "list")
	require.Equal(t, 0, code, stderr)
	items := mustJSON(t, stdout)["items"].([]any)
	require.NotEmpty(t, items)
	for _, raw := range items {
		item := raw.(map[string]any)
		require.Contains(t, item, "copy", "every row is the API's JSON")
		want := map[string]string{"region-a": "a", "region-b": "b"}[item["region_id"].(string)]
		if item["id"] == "s2" {
			want = "b2"
		}
		require.Equal(t, want, item["copy"], item)
	}
}

// whoami omits what it does not know: with no credential there is no
// credential_source.
func TestWhoamiWithoutACredentialOmitsCredentialSource(t *testing.T) {
	stdout, stderr, code := runReported(t, cfgDir(t), nil, "--api-url", "http://127.0.0.1:1", "whoami")
	require.Equal(t, 0, code, stderr)
	out := mustJSON(t, stdout)
	require.NotContains(t, out, "credential_source")
	require.Equal(t, "default", out["profile"])
}

// --region narrows the list to that region even when regions share one API.
func TestRegionFlagFiltersListsOnASharedAPI(t *testing.T) {
	regionsBody := `{"regions":[{"id":"earth","name":"Earth","available":false,"parent_region_ids":[]},{"id":"us","name":"US","available":true,"parent_region_ids":["earth"]},{"id":"eu","name":"EU","available":true,"parent_region_ids":["earth"]}],"default_region_id":null}`
	f := newFakeAPI(t)
	f.on(http.MethodGet, "/regions", 200, regionsBody)
	f.handle(http.MethodGet, "/sandboxes", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"sandboxes":[{"id":"a-us","name":"a","status":"running","region_id":"us"},{"id":"b-eu","name":"b","status":"running","region_id":"eu"}],"next_cursor":"c2"}`))
	})
	f.handle(http.MethodGet, "/images", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"images":[{"id":"i-us","status":"ready","region_id":"us","region_ids":["us"]},{"id":"i-eu","status":"ready","region_id":"eu","region_ids":["eu"]},{"id":"i-both","status":"ready","region_id":"eu","region_ids":["eu","us"]}]}`))
	})
	ids := func(stdout string) []string {
		var out []string
		for _, item := range mustJSON(t, stdout)["items"].([]any) {
			out = append(out, item.(map[string]any)["id"].(string))
		}
		return out
	}
	env := map[string]string{"DAYTONA_TEST_SKIP_CONTRACT": "1"}
	stdout, stderr, code := runReported(t, cfgDir(t), env, "--api-url", f.srv.URL, "--api-key", "k", "sandbox", "list", "--region", "us")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, []string{"a-us"}, ids(stdout))
	require.NotEmpty(t, mustJSON(t, stdout)["next_cursor"], "the cursor still advances over the API's page")
	stdout, stderr, code = runReported(t, cfgDir(t), env, "--api-url", f.srv.URL, "--api-key", "k", "image", "list", "--region", "us")
	require.Equal(t, 0, code, stderr)
	require.ElementsMatch(t, []string{"i-us", "i-both"}, ids(stdout))
	// A parent region selects the regions inside it.
	stdout, stderr, code = runReported(t, cfgDir(t), env, "--api-url", f.srv.URL, "--api-key", "k", "sandbox", "list", "--region", "earth")
	require.Equal(t, 0, code, stderr)
	require.ElementsMatch(t, []string{"a-us", "b-eu"}, ids(stdout))
	// Without --region nothing is filtered.
	stdout, stderr, code = runReported(t, cfgDir(t), env, "--api-url", f.srv.URL, "--api-key", "k", "sandbox", "list")
	require.Equal(t, 0, code, stderr)
	require.ElementsMatch(t, []string{"a-us", "b-eu"}, ids(stdout))
}

// --region on a parent region asks every API that serves a region inside it:
// a child region with its own API is not missed.
func TestRegionFlagOnAParentAsksEveryChildAPI(t *testing.T) {
	var usQueries atomic.Int32
	us := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		usQueries.Add(1)
		w.Header().Set("Content-Type", "application/json")
		if strings.HasSuffix(r.URL.Path, "/images") {
			_, _ = w.Write([]byte(`{"images":[{"id":"i-us","status":"ready","region_id":"us","region_ids":["us"]}]}`))
			return
		}
		_, _ = w.Write([]byte(`{"sandboxes":[{"id":"sb1-us","name":"a","status":"running","region_id":"us"}],"next_cursor":"u2"}`))
	}))
	defer us.Close()
	f := newFakeAPI(t)
	f.on(http.MethodGet, "/regions", 200, `{"regions":[{"id":"earth","name":"Earth","available":false,"parent_region_ids":[]},{"id":"us","name":"US","available":true,"parent_region_ids":["earth"],"api_base_url":"`+us.URL+`"},{"id":"eu","name":"EU","available":true,"parent_region_ids":["earth"]},{"id":"mars","name":"Mars","available":true,"parent_region_ids":[]}],"default_region_id":null}`)
	f.handle(http.MethodGet, "/sandboxes", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"sandboxes":[{"id":"sb2-eu","name":"b","status":"running","region_id":"eu"},{"id":"sb3-mars","name":"c","status":"running","region_id":"mars"}]}`))
	})
	f.handle(http.MethodGet, "/images", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"images":[{"id":"i-eu","status":"ready","region_id":"eu","region_ids":["eu"]},{"id":"i-mars","status":"ready","region_id":"mars","region_ids":["mars"]}]}`))
	})
	ids := func(stdout string) []string {
		var out []string
		for _, item := range mustJSON(t, stdout)["items"].([]any) {
			out = append(out, item.(map[string]any)["id"].(string))
		}
		return out
	}
	env := map[string]string{"DAYTONA_TEST_SKIP_CONTRACT": "1"}
	stdout, stderr, code := runReported(t, cfgDir(t), env, "--api-url", f.srv.URL, "--api-key", "k", "sandbox", "list", "--region", "earth")
	require.Equal(t, 0, code, stderr)
	require.ElementsMatch(t, []string{"sb1-us", "sb2-eu"}, ids(stdout))
	next := mustJSON(t, stdout)["next_cursor"].(string)
	require.Contains(t, next, "us:u2", "the child API's cursor is kept")
	homeQueries := len(f.requests)
	stdout, stderr, code = runReported(t, cfgDir(t), env, "--api-url", f.srv.URL, "--api-key", "k", "sandbox", "list", "--region", "earth", "--cursor", next)
	require.Equal(t, 0, code, stderr)
	require.Equal(t, []string{"sb1-us"}, ids(stdout), "resuming asks only the unfinished child API")
	for _, r := range f.requests[homeQueries:] {
		require.NotEqual(t, "/sandboxes", r.Path)
	}
	usQueries.Store(0)
	stdout, stderr, code = runReported(t, cfgDir(t), env, "--api-url", f.srv.URL, "--api-key", "k", "image", "list", "--region", "earth")
	require.Equal(t, 0, code, stderr)
	require.ElementsMatch(t, []string{"i-us", "i-eu"}, ids(stdout))
	require.Equal(t, int32(1), usQueries.Load())
	// A region whose scope holds no other API asks only its own.
	stdout, stderr, code = runReported(t, cfgDir(t), env, "--api-url", f.srv.URL, "--api-key", "k", "sandbox", "list", "--region", "eu")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, []string{"sb2-eu"}, ids(stdout))
	require.Equal(t, int32(1), usQueries.Load())
}

// Login modes conflict only when both are selected: false values are fine.
func TestLoginModeFlagsConflictOnlyWhenSelected(t *testing.T) {
	for _, args := range [][]string{
		{"login", "--device", "--browser"},
		{"login", "--device=true", "--browser=true"},
		{"login", "--token", "dtn_x", "--device"},
		{"login", "--token", "dtn_x", "--client-id", "c"},
	} {
		_, stderr, code := runReported(t, cfgDir(t), nil, args...)
		require.Equal(t, cerr.ExitUsage, code, "%v: %s", args, stderr)
	}
	_, stderr, code := runReported(t, cfgDir(t), nil, "login", "--token", "dtn_x", "--device=false", "--browser=false")
	require.Equal(t, 0, code, stderr)
	// A false --device does not conflict with --browser: the browser flow
	// starts (and fails here, with no API to reach), not a usage error.
	_, stderr, code = runReported(t, cfgDir(t), nil, "--api-url", "http://127.0.0.1:1", "login", "--device=false", "--browser")
	require.NotEqual(t, cerr.ExitUsage, code, stderr)
}

// The old lifecycle --timeout SECONDS names its replacement.
func TestOldLifecycleTimeoutNamesItsReplacement(t *testing.T) {
	for _, args := range [][]string{
		{"sandbox", "create", "--timeout", "3600"},
		{"sandbox", "create", "--timeout", "+3600"},
		{"sandbox", "create-macos", "--timeout", "60"},
		{"sandbox", "update", "s", "--timeout", "3600"},
	} {
		_, stderr, code := runReported(t, cfgDir(t), nil, args...)
		require.Equal(t, cerr.ExitUsage, code, args)
		require.Contains(t, errorObject(t, stderr)["hint"], "--lifecycle-timeout", args)
	}
	_, stderr, code := runReported(t, cfgDir(t), nil, "sandbox", "wait", "s", "--timeout", "30")
	require.Equal(t, cerr.ExitUsage, code)
	require.NotContains(t, stderr, "--lifecycle-timeout", "sandbox wait never had a lifecycle --timeout")
}

// image create's source is a oneOf: its type picks the member, whose fields
// are exact; an unknown type is refused.
func TestImageCreateSourceMembersAreChecked(t *testing.T) {
	dir := t.TempDir()
	for body, want := range map[string]string{
		`{"source":{"type":"oci","ref":"x"}}`:                "unknown type",
		`{"source":{"ref":"x"}}`:                             `"type" is required`,
		`{"source":{"Type":"registry","ref":"x"}}`:           `did you mean "type"`,
		`{"source":{"type":"registry","ref":"x","extra":1}}`: `unknown field "extra"`,
		`{"source":{"type":"registry","Ref":"x"}}`:           `did you mean "ref"`,
		`{"source":{"type":"named","ref":"x"}}`:              `unknown field "ref"`,
	} {
		path := filepath.Join(dir, "body.json")
		require.NoError(t, os.WriteFile(path, []byte(body), 0o600))
		f := newFakeAPI(t)
		_, stderr, code := runReported(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "k", "image", "create", "img", "--file", path, "--no-wait")
		require.Equal(t, cerr.ExitUsage, code, body)
		obj := errorObject(t, stderr)
		require.Contains(t, fmt.Sprint(obj["message"], " ", obj["hint"]), want, body)
		require.Empty(t, f.requests, body)
	}
	path := filepath.Join(dir, "body.json")
	require.NoError(t, os.WriteFile(path, []byte(`{"source":{"type":"named","name":"base"}}`), 0o600))
	_, _, err := runCLI(t, []string{"image", "create", "img", "--file", path, "--no-wait"}, []expectedRequest{{
		method: http.MethodPost, path: "/images", status: 202,
		bodyJSON: `{"name":"img","source":{"type":"named","name":"base"}}`,
		response: `{"id":"11111111-1111-1111-1111-111111111111","status":"pending"}`,
	}})
	require.NoError(t, err)
}

// Flag combinations a command would accept and ignore are usage errors,
// before the configuration loads (an unknown profile does not hide them).
func TestIgnoredFlagCombinationsAreRefused(t *testing.T) {
	for _, args := range [][]string{
		{"sandbox", "update", "s", "--clear-labels", "--label", "a=b"},
		{"sandbox", "process", "signal", "s", "p", "TERM", "--escalate-to", "KILL"},
		{"sandbox", "process", "signal", "s", "p", "TERM", "--escalate-to", "KILL", "--escalate-after", ""},
		{"sandbox", "process", "signal", "s", "p", "TERM", "--escalate-to", "KILL", "--escalate-after", " "},
		{"login", "--token", "dtn_x", "--device"},
	} {
		f := newFakeAPI(t)
		for _, env := range []map[string]string{nil, {"DAYTONA_PROFILE": "missing"}} {
			_, stderr, code := runReported(t, cfgDir(t), env, append([]string{"--api-url", f.srv.URL, "--api-key", "k"}, args...)...)
			require.Equal(t, cerr.ExitUsage, code, "%v %v: %s", args, env, stderr)
		}
		require.Empty(t, f.requests, args)
	}
}

// secret and registry commands never print a write-only credential, even
// when a response carries one: every other field passes through.
func TestSecretAndRegistryOutputNeverHoldsTheCredential(t *testing.T) {
	t.Setenv("DAYTONA_TEST_SKIP_CONTRACT", "1")
	secret := `{"name":"API_KEY","allowed_domains":["a.example"],"created_at":"2026-11-08T00:00:00Z","updated_at":"2026-11-08T00:00:00Z","value":"s3cr3t-v4lue","Value":"s3cr3t-v4lue","future":1}`
	registry := `{"host":"ghcr.io","kind":"basic","username":"me","created_at":"2026-11-08T00:00:00Z","updated_at":"2026-11-08T00:00:00Z","password":"s3cr3t-v4lue","future":1}`
	f := newFakeAPI(t)
	reply := func(body string) http.HandlerFunc {
		return func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte(body)) }
	}
	f.handle(http.MethodGet, "/secrets/API_KEY", reply(secret))
	f.handle(http.MethodPut, "/secrets/API_KEY", reply(secret))
	f.handle(http.MethodGet, "/secrets", reply(`{"secrets":[`+secret+`]}`))
	f.handle(http.MethodGet, "/registries/ghcr.io", reply(registry))
	f.handle(http.MethodPut, "/registries/ghcr.io", reply(registry))
	f.handle(http.MethodGet, "/registries", reply(`{"registries":[`+registry+`]}`))
	for _, args := range [][]string{
		{"secret", "get", "API_KEY"},
		{"secret", "set", "API_KEY", "--value", "x"},
		{"secret", "list"},
		{"registry", "get", "ghcr.io"},
		{"registry", "set", "ghcr.io", "--username", "me", "--password", "x"},
		{"registry", "list"},
	} {
		for _, format := range []string{"json", "yaml", "table"} {
			stdout, stderr, code := runReported(t, cfgDir(t), nil, append([]string{"--api-url", f.srv.URL, "--api-key", "k", "--project", "p", "-o", format}, args...)...)
			require.Equal(t, 0, code, "%v %s: %s", args, format, stderr)
			require.NotContains(t, stdout, "s3cr3t", "%v %s", args, format)
			if format != "table" {
				require.Contains(t, stdout, "future", "%v %s: other fields pass through", args, format)
			}
		}
	}
}
