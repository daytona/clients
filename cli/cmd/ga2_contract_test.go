// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

const ga2SandboxID = "11111111-1111-1111-1111-111111111111"

const ga2CreateAck = `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"` + ga2SandboxID + `","name":"box","status":"pending","wait_cap_seconds":3,"access_token":"sandbox-token-must-not-print","access_token_expires_at":"2026-09-13T00:05:00Z"}`

// --no-wait prints the sandbox detail (the shape `sandbox get` prints), never
// the create acknowledgement and its access token.
func TestSandboxCreate_noWaitPrintsDetailWithoutAccessToken(t *testing.T) {
	stdout, stderr, err := runCLI(t, []string{"sandbox", "create", "--no-wait"}, []expectedRequest{
		{method: http.MethodPost, path: "/sandboxes", status: 202, bodyJSON: `{}`, response: ga2CreateAck},
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", "pending")},
	})
	require.NoError(t, err)
	require.NotContains(t, stdout+stderr, "sandbox-token-must-not-print")
	require.NotContains(t, stdout, "access_token")
	got := requireJSONStdout(t, stdout)
	require.Equal(t, "linux", got["class"])
	require.Equal(t, "pending", got["status"])
}

// A failed wait after an accepted create names the sandbox it made.
func TestSandboxCreate_failedWaitNamesTheCreatedSandbox(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "create", "--timeout", "1s"}, []expectedRequest{
		{method: http.MethodPost, path: "/sandboxes", status: 202, bodyJSON: `{}`, response: ga2CreateAck},
		{method: http.MethodGet, path: "/regions", status: 200, response: `{"regions":[],"default_region_id":null}`},
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 403, response: `{"code":"FORBIDDEN","message":"no","source":"DAYTONA_API"}`},
	})
	require.Error(t, err)
	require.Equal(t, cerr.ExitForbidden, cerr.ExitCode(err))
	require.Contains(t, err.Error(), "sandbox "+ga2SandboxID+" was created")
	require.NotContains(t, err.Error(), "sandbox-token-must-not-print")
}

func TestSandboxCreate_nameStampsLabelAndDerivesIdempotencyKey(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "create", "--name", "web", "--label", "k=v", "--no-wait"}, []expectedRequest{
		{method: http.MethodPost, path: "/sandboxes", status: 202, bodyJSON: `{"name":"web","labels":{"k":"v","name":"web"}}`, response: ga2CreateAck,
			check: func(t *testing.T, r *http.Request, _ []byte) {
				assert.Equal(t, "name:web", r.Header.Get("Idempotency-Key"))
			}},
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", "pending")},
	})
	require.NoError(t, err)

	// --idempotency-key overrides the derived key.
	_, _, err = runCLI(t, []string{"sandbox", "create-from-snapshot", "snap", "--name", "web", "--idempotency-key", "mine", "--no-wait"}, []expectedRequest{
		{method: http.MethodPost, path: "/sandboxes/from-snapshot", status: 202, bodyJSON: `{"snapshot":"snap","name":"web","labels":{"name":"web"}}`, response: ga2CreateAck,
			check: func(t *testing.T, r *http.Request, _ []byte) {
				assert.Equal(t, "mine", r.Header.Get("Idempotency-Key"))
			}},
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", "pending")},
	})
	require.NoError(t, err)
}

func TestSandboxCreate_unnamedCreateSendsAFreshIdempotencyKey(t *testing.T) {
	var keys []string
	for range 2 {
		_, _, err := runCLI(t, []string{"sandbox", "create", "--no-wait"}, []expectedRequest{
			{method: http.MethodPost, path: "/sandboxes", status: 202, bodyJSON: `{}`, response: ga2CreateAck,
				check: func(_ *testing.T, r *http.Request, _ []byte) { keys = append(keys, r.Header.Get("Idempotency-Key")) }},
			{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", "pending")},
		})
		require.NoError(t, err)
	}
	require.Len(t, keys, 2)
	require.Regexp(t, uuidRe, keys[0])
	require.NotEqual(t, keys[0], keys[1])
}

func TestSandboxCreate_serverErrorHintNamesTheRetryKey(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "create", "--name", "web"}, []expectedRequest{
		{method: http.MethodPost, path: "/sandboxes", status: 503, bodyJSON: `{"name":"web","labels":{"name":"web"}}`, response: `{"code":"SERVICE_UNAVAILABLE","message":"busy","source":"DAYTONA_API"}`},
	})
	require.Error(t, err)
	var ce *cerr.Error
	require.True(t, errors.As(err, &ce))
	require.Contains(t, ce.Hint, "--idempotency-key name:web")
}

func TestIdempotencyKeyForName_matchesTheSDKs(t *testing.T) {
	require.Equal(t, "name:web-1", idempotencyKeyForName("web-1"))
	long := strings.Repeat("a", 252)
	require.Equal(t, "name:sha256:", idempotencyKeyForName(long)[:12])
	require.Equal(t, "name:"+strings.Repeat("a", 251), idempotencyKeyForName(strings.Repeat("a", 251)))
	// Same as the SDKs: sha256 over the UTF-8 bytes for a non-ASCII name.
	// sha256 over the UTF-8 bytes for a name that is not printable ASCII.
	require.Equal(t, "name:sha256:850f7dc43910ff890f8879c0ed26fe697c93a067ad93a7d50f466a7028a9bf4e", idempotencyKeyForName("café"))
	require.Equal(t, "name:sha256:", idempotencyKeyForName(" web")[:12])
}

func TestSandboxCreate_nameAndKeyChecks(t *testing.T) {
	for _, tc := range []struct {
		args []string
		want string
	}{
		{[]string{"sandbox", "create", "--name", "  "}, "--name (name) must not be blank"},
		{[]string{"sandbox", "create", "--name", "web", "--label", "name=other"}, "disagrees with --name"},
		{[]string{"sandbox", "create", "--idempotency-key", ""}, "--idempotency-key must not be blank"},
		{[]string{"sandbox", "create", "--idempotency-key", "k\x01"}, "printable ASCII"},
		{[]string{"sandbox", "create", "--wait", "5s"}, "unknown flag: --wait"},
		{[]string{"sandbox", "create", "--server-wait", "5s", "--no-wait"}, "--server-wait has no effect with --no-wait"},
	} {
		_, _, err := runCLI(t, tc.args, nil)
		require.Error(t, err, "%v", tc.args)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), "%v", tc.args)
		require.ErrorContains(t, err, tc.want, "%v", tc.args)
	}
	var ce *cerr.Error
	_, _, err := runCLI(t, []string{"sandbox", "create", "--wait", "5s"}, nil)
	require.ErrorAs(t, err, &ce)
	require.Contains(t, ce.Hint, "--server-wait")
}

func TestSandboxCreate_serverWaitSetsWaitSeconds(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "create", "--server-wait", "9s", "--no-wait"}, nil)
	require.ErrorContains(t, err, "--server-wait has no effect with --no-wait")
	_, _, err = runCLI(t, []string{"sandbox", "create", "--server-wait", "0s", "--timeout", "1s"}, []expectedRequest{
		{method: http.MethodPost, path: "/sandboxes", status: 202, bodyJSON: `{"wait_seconds":0}`, response: ga2CreateAck},
		{method: http.MethodGet, path: "/regions", status: 200, response: `{"regions":[],"default_region_id":null}`},
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", "running")},
	})
	require.NoError(t, err)
}

func TestSandboxCreateMacos_localChecksBeforeConfirmation(t *testing.T) {
	for _, tc := range []struct {
		args []string
		want string
	}{
		{[]string{"--cores", "2"}, "--cores (cores) must be at least 4"},
		{[]string{"--memory", "1024"}, "--memory (memory_mib) must be at least 4096"},
		{[]string{"--disk", "1000"}, "--disk (disk_mib) must be at least 81920"},
		{[]string{"--auto-renew"}, "--auto-renew (auto_renew) applies only with --reservation new"},
		{[]string{"--volume", "v:/m", "--persistent"}, "cannot be persistent"},
	} {
		args := append([]string{"sandbox", "create-macos", "--image", "base-macos", "--no-wait"}, tc.args...)
		_, _, err := runCLI(t, args, nil)
		require.Error(t, err, "%v", tc.args)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), "%v", tc.args)
		require.ErrorContains(t, err, tc.want, "%v", tc.args)
	}
}

func TestSandboxWait_forValidatesAndAcceptsSDKNames(t *testing.T) {
	for _, value := range []string{"bogus", "RUNNING", "", "booting", "failed"} {
		_, _, err := runCLI(t, []string{"sandbox", "wait", ga2SandboxID, "--for", value}, nil)
		require.Error(t, err, value)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), value)
		require.ErrorContains(t, err, "invalid --for", value)
	}
	for value, wire := range map[string]string{"started": "running", "destroyed": "deleted"} {
		key := "wait"
		if wire == "deleted" {
			key = "wait_exact"
		}
		_, _, err := runCLI(t, []string{"sandbox", "wait", ga2SandboxID, "--for", value, "--timeout", "1s"}, []expectedRequest{
			{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", "running")},
			{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", wire),
				check: func(t *testing.T, r *http.Request, _ []byte) { assert.Equal(t, wire, r.URL.Query().Get(key)) }},
		})
		require.NoError(t, err, value)
	}
}

func TestPreviewURLExpire_doesNotEchoTheToken(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "port", "preview-url", "expire", ga2SandboxID, "8080", "secret-preview-token"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", "running")},
		{method: http.MethodPost, path: "/sandboxes/" + ga2SandboxID + "/ports/8080/signed-preview-urls/revoke", status: 204, bodyJSON: `{"token":"secret-preview-token"}`},
	})
	require.NoError(t, err)
	require.NotContains(t, stdout, "secret-preview-token")
	got := requireJSONStdout(t, stdout)
	require.Equal(t, float64(8080), got["port"])
	require.Equal(t, true, got["revoked"])
	_, hasToken := got["token"]
	require.False(t, hasToken)

	for _, args := range [][]string{
		{"sandbox", "port", "preview-url", "expire", ga2SandboxID, "8080", " "},
		{"sandbox", "port", "preview-url", "expire", ga2SandboxID, "abc", "t"},
		{"sandbox", "port", "preview-url", ga2SandboxID, "65536"},
		{"sandbox", "port", "preview-url", ga2SandboxID, "0"},
	} {
		_, _, err := runCLI(t, args, nil)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), "%v", args)
	}
}

func TestSandboxUpdate_keepsTheNameLabel(t *testing.T) {
	named := strings.Replace(sandboxDetailFixture(ga2SandboxID, "linux", "running"), `"labels":{}`, `"labels":{"name":"box","old":"1"}`, 1)
	_, _, err := runCLI(t, []string{"sandbox", "update", ga2SandboxID, "--label", "env=dev"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: named},
		{method: http.MethodPut, path: "/sandboxes/" + ga2SandboxID + "/labels", status: 200, bodyJSON: `{"labels":{"env":"dev","name":"box"}}`, response: `{"id":"` + ga2SandboxID + `","labels":{"env":"dev","name":"box"}}`},
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: named},
	})
	require.NoError(t, err)

	_, _, err = runCLI(t, []string{"sandbox", "update", ga2SandboxID, "--clear-labels"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: named},
		{method: http.MethodPut, path: "/sandboxes/" + ga2SandboxID + "/labels", status: 200, bodyJSON: `{"labels":{"name":"box"}}`, response: `{"id":"` + ga2SandboxID + `","labels":{"name":"box"}}`},
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: named},
	})
	require.NoError(t, err)

	_, _, err = runCLI(t, []string{"sandbox", "update", ga2SandboxID, "--label", "name=other"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: named},
	})
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.ErrorContains(t, err, "must match the sandbox name")
}

func TestSandboxStart_isStateAware(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "start", ga2SandboxID, "--timeout", "1s"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", "running")},
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", "running")},
	})
	require.NoError(t, err)

	_, _, err = runCLI(t, []string{"sandbox", "start", ga2SandboxID, "--no-wait"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", "paused")},
		{method: http.MethodPost, path: "/sandboxes/" + ga2SandboxID + "/resume", status: 202, response: `{"id":"` + ga2SandboxID + `","status":"resuming"}`},
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", "booting")},
	})
	require.NoError(t, err)
}

func TestSandboxDelete_noWaitPrintsDetailOrTheOperationWhenGone(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "delete", ga2SandboxID, "--yes", "--no-wait"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", "running")},
		{method: http.MethodDelete, path: "/sandboxes/" + ga2SandboxID, status: 202, response: `{"status":"deleting","id":"` + ga2SandboxID + `"}`},
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", "deleting")},
	})
	require.NoError(t, err)
	require.Equal(t, "linux", requireJSONStdout(t, stdout)["class"])

	stdout, _, err = runCLI(t, []string{"sandbox", "delete", ga2SandboxID, "--yes", "--no-wait"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", "running")},
		{method: http.MethodDelete, path: "/sandboxes/" + ga2SandboxID, status: 202, response: `{"status":"deleting","id":"` + ga2SandboxID + `"}`},
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 404, response: `{"code":"NOT_FOUND","message":"gone","source":"DAYTONA_API"}`},
	})
	require.NoError(t, err)
	require.Equal(t, map[string]any{"id": ga2SandboxID, "status": "deleting"}, requireJSONStdout(t, stdout))
}

func TestSandboxList_cursorNamingAnUnknownRegionIsRefused(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "list", "--cursor", "nosuchregion:abc"}, []expectedRequest{
		{method: http.MethodGet, path: "/regions", status: 200, response: `{"regions":[{"id":"region-b","name":"Region B","available":true,"parent_region_ids":["earth"]}],"default_region_id":null}`},
	})
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.ErrorContains(t, err, `names region "nosuchregion"`)
}

func TestSandboxCP_localSourceIsCheckedBeforeAnyRequest(t *testing.T) {
	dir := t.TempDir()
	file := filepath.Join(dir, "a.txt")
	require.NoError(t, os.WriteFile(file, []byte("x"), 0o600))
	for _, tc := range []struct {
		args []string
		want string
	}{
		{[]string{"sandbox", "cp", dir, ga2SandboxID + ":/tmp/x"}, "is a directory"},
		{[]string{"sandbox", "cp", filepath.Join(dir, "missing"), ga2SandboxID + ":/tmp/x"}, "no such file or directory"},
		{[]string{"sandbox", "cp", "-r", file, ga2SandboxID + ":/tmp/x"}, "is not a directory"},
	} {
		_, _, err := runCLI(t, tc.args, nil)
		require.Error(t, err, "%v", tc.args)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), "%v", tc.args)
		require.ErrorContains(t, err, tc.want, "%v", tc.args)
	}
}

func TestProcessExitError_signalsExit128PlusTheirNumber(t *testing.T) {
	signaled := tbgen.ReasonSignaled
	for signal, want := range map[string]int{"INT": 130, "KILL": 137, "TERM": 143, "HUP": 129, "SIGSEGV": 139, "NOPE": 1} {
		sig := signal
		err := processExitError(tbgen.ExitResult{Reason: &signaled, Signal: &sig})
		require.Equal(t, want, cerr.ExitCode(err), signal)
	}
}

func TestInterruptedExitCode_sigtermIs143(t *testing.T) {
	ctx, cancel := context.WithCancelCause(context.Background())
	cancel(errSignalled{Signal: syscall.SIGTERM})
	require.Equal(t, cerr.ExitTerminated, interruptedExitCode(ctx, cerr.ExitInterrupted))
	require.Equal(t, cerr.ExitUsage, interruptedExitCode(ctx, cerr.ExitUsage))

	ctx, cancel = context.WithCancelCause(context.Background())
	cancel(errSignalled{Signal: os.Interrupt})
	require.Equal(t, cerr.ExitInterrupted, interruptedExitCode(ctx, cerr.ExitInterrupted))
	require.Equal(t, cerr.ExitInterrupted, interruptedExitCode(context.Background(), cerr.ExitInterrupted))
}

func TestAPIURL_invalidValueIsAUsageError(t *testing.T) {
	for _, value := range []string{"ftp://api.example", "not a url", "api.example", "http://"} {
		_, errb, code := run(t, cfgDir(t), nil, "sandbox", "list", "--api-key", "k", "--api-url", value)
		require.Equal(t, cerr.ExitUsage, code, "%s: %s", value, errb)
		require.Contains(t, errb, "invalid API URL", value)
	}
}

// A saved credential is never sent to another API URL; an explicit key is.
func TestSavedCredentialStaysWithItsAPIURL(t *testing.T) {
	saved := newFakeAPI(t)
	other := newFakeAPI(t)
	d := cfgDir(t)
	_, errb, code := run(t, d, nil, "login", "--token", "saved-token", "--api-url", saved.srv.URL)
	require.Equal(t, 0, code, errb)

	for _, args := range [][]string{
		{"sandbox", "list", "--api-url", other.srv.URL},
		{"whoami", "--api-url", other.srv.URL},
	} {
		_, errb, code = run(t, d, nil, args...)
		require.Equal(t, cerr.ExitUsage, code, "%v: %s", args, errb)
		require.Contains(t, errb, "is not sent to "+other.srv.URL)
	}
	_, errb, code = run(t, d, map[string]string{"DAYTONA_V2_API_URL": other.srv.URL}, "sandbox", "list")
	require.Equal(t, cerr.ExitUsage, code, errb)
	for _, r := range other.requests {
		require.NotContains(t, r.Auth, "saved-token", "%s %s", r.Method, r.Path)
	}

	other.on("GET", "/api-keys/current", 401, `{"code":"UNAUTHORIZED","message":"bad key","source":"DAYTONA_API"}`)
	_, errb, code = run(t, d, nil, "whoami", "--api-url", other.srv.URL, "--api-key", "explicit")
	require.Equal(t, cerr.ExitAuth, code, errb)
	require.NotEmpty(t, other.requests)
	require.Equal(t, "Bearer explicit", other.requests[len(other.requests)-1].Auth)
}

const ga2KeyProject = "44444444-4444-4444-4444-444444444444"

func projectKeyFixture() map[string]any {
	return map[string]any{
		"id": "99999999-9999-9999-9999-999999999999", "name": "ci", "created_at": "2026-09-13T00:00:00Z",
		"project_id": ga2KeyProject, "project_permissions": []string{}, "sync_to_v0": false,
		"context": map[string]any{"org_id": "22222222-2222-2222-2222-222222222222", "org_name": "Acme", "project_name": "lab"},
	}
}

func TestStaticKey_projectOverrideMustMatchTheKey(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/api-keys/current", 200, projectKeyFixture())
	f.on("GET", "/regions", 200, map[string]any{"regions": []any{}, "default_region_id": nil})
	f.on("GET", "/sandboxes", 200, map[string]any{"sandboxes": []any{}})
	base := []string{"--api-url", f.srv.URL, "--api-key", "k"}

	_, errb, code := run(t, cfgDir(t), nil, append(base, "--project", "other", "sandbox", "list")...)
	require.Equal(t, cerr.ExitUsage, code, errb)
	require.Contains(t, errb, `--project "other" is not the project this API key acts in`)
	_, errb, code = run(t, cfgDir(t), map[string]string{"DAYTONA_PROJECT": "other"}, append(base, "whoami", "-o", "json")...)
	require.Equal(t, cerr.ExitUsage, code, errb)

	for _, project := range []string{"lab", ga2KeyProject, strings.ToUpper(ga2KeyProject)} {
		_, errb, code = run(t, cfgDir(t), nil, append(base, "--project", project, "sandbox", "list", "-o", "json")...)
		require.Equal(t, 0, code, "%s: %s", project, errb)
	}
	// Without a named project, no key lookup is needed.
	n := 0
	for _, r := range f.requests {
		if r.Path == "/api-keys/current" {
			n++
		}
	}
	_, errb, code = run(t, cfgDir(t), nil, append(base, "sandbox", "list", "-o", "json")...)
	require.Equal(t, 0, code, errb)
	m := 0
	for _, r := range f.requests {
		if r.Path == "/api-keys/current" {
			m++
		}
	}
	require.Equal(t, n, m)
}

func TestWhoami_staticKeyReportsTheKeysContext(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/api-keys/current", 200, projectKeyFixture())
	out, errb, code := run(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "k", "whoami", "-o", "json")
	require.Equal(t, 0, code, errb)
	var got map[string]any
	require.NoError(t, json.Unmarshal([]byte(out), &got))
	require.Equal(t, ga2KeyProject, got["project_id"])
	require.Equal(t, "22222222-2222-2222-2222-222222222222", got["org_id"])
	require.Equal(t, true, got["context_resolved"])
	require.NotNil(t, got["api_key"])

	f2 := newFakeAPI(t)
	f2.on("GET", "/api-keys/current", 401, `{"code":"UNAUTHORIZED","message":"bad key","source":"DAYTONA_API"}`)
	_, _, code = run(t, cfgDir(t), nil, "--api-url", f2.srv.URL, "--api-key", "bad", "whoami", "-o", "json")
	require.Equal(t, cerr.ExitAuth, code)

	f3 := newFakeAPI(t)
	f3.on("GET", "/api-keys/current", 403, `{"code":"FORBIDDEN","message":"not a project key","source":"DAYTONA_API"}`)
	out, errb, code = run(t, cfgDir(t), nil, "--api-url", f3.srv.URL, "--api-key", "orgkey", "whoami", "-o", "json")
	require.Equal(t, 0, code, errb)
	require.NotContains(t, out, "api_key")
}

// A dangling active_profile behaves like an unknown --profile on commands that
// use it, and does not stop the profile commands.
func TestDanglingActiveProfileIsNotFound(t *testing.T) {
	d := cfgDir(t)
	require.NoError(t, os.WriteFile(filepath.Join(d, "config.yaml"), []byte("active_profile: ghost\nprofiles: []\n"), 0o600))
	_, errb, code := run(t, d, nil, "whoami", "-o", "json")
	require.Equal(t, cerr.ExitNotFound, code, errb)
	require.Contains(t, errb, `no profile "ghost"`)
	_, errb, code = run(t, d, nil, "profile", "list", "-o", "json")
	require.Equal(t, 0, code, errb)
	_, errb, code = run(t, d, nil, "login", "--token", "dtn_k")
	require.Equal(t, 0, code, errb)
}

func TestOutputFlag_explicitEmptyIsAUsageError(t *testing.T) {
	for _, value := range []string{"", " ", "JSON"} {
		_, errb, code := run(t, cfgDir(t), nil, "whoami", "-o", value)
		require.Equal(t, cerr.ExitUsage, code, "%q: %s", value, errb)
	}
}

func TestProcessExitError_signalWithoutReasonStillExitsBySignal(t *testing.T) {
	sig := "TERM"
	require.Equal(t, 143, cerr.ExitCode(processExitError(tbgen.ExitResult{Signal: &sig})))
	unknown := tbgen.Reason("evicted")
	require.Equal(t, 143, cerr.ExitCode(processExitError(tbgen.ExitResult{Reason: &unknown, Signal: &sig})))
}

// A --no-wait create whose follow-up read fails still succeeds (the create
// did), printing the credential-free subset of the acknowledgement.
func TestSandboxCreate_noWaitReadFailureFallsBackToTheAck(t *testing.T) {
	stdout, stderr, err := runCLI(t, []string{"sandbox", "create", "--no-wait"}, []expectedRequest{
		{method: http.MethodPost, path: "/sandboxes", status: 202, bodyJSON: `{}`, response: ga2CreateAck},
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 403, response: `{"code":"FORBIDDEN","message":"no","source":"DAYTONA_API"}`},
	})
	require.NoError(t, err)
	require.Equal(t, map[string]any{"id": ga2SandboxID, "name": "box", "status": "pending"}, requireJSONStdout(t, stdout))
	require.Contains(t, stderr, "was created, but reading it failed")
	require.NotContains(t, stdout+stderr, "sandbox-token-must-not-print")
}

func TestPreviewURLExpire_errorNeverEchoesTheToken(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "port", "preview-url", "expire", ga2SandboxID, "8080", "secret-preview-token"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", "running")},
		{method: http.MethodPost, path: "/sandboxes/" + ga2SandboxID + "/ports/8080/signed-preview-urls/revoke", status: 400, bodyJSON: `{"token":"secret-preview-token"}`,
			response: `{"code":"BAD_REQUEST","message":"unknown token secret-preview-token","source":"DAYTONA_API"}`},
	})
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.NotContains(t, err.Error(), "secret-preview-token")
}

func TestSavedCredential_urlComparisonIgnoresSchemeAndHostCase(t *testing.T) {
	saved := newFakeAPI(t)
	saved.on("GET", "/api-keys/current", 200, projectKeyFixture())
	d := cfgDir(t)
	// A hostname (not an IP literal), so host casing is actually compared.
	named := strings.Replace(saved.srv.URL, "127.0.0.1", "localhost", 1)
	_, errb, code := run(t, d, nil, "login", "--token", "saved-token", "--api-url", named)
	require.Equal(t, 0, code, errb)
	upper := strings.ToUpper(named)
	require.NotEqual(t, named, upper)
	_, errb, code = run(t, d, nil, "whoami", "-o", "json", "--api-url", upper)
	require.Equal(t, 0, code, errb)
}

func TestSandboxStart_transientStatusOnlyWaits(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "start", ga2SandboxID, "--timeout", "1s"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", "booting")},
		{method: http.MethodGet, path: "/sandboxes/" + ga2SandboxID, status: 200, response: sandboxDetailFixture(ga2SandboxID, "linux", "running")},
	})
	require.NoError(t, err)
}

func TestSandboxSearch_repeatedLabelWithDifferentValuesIsRefused(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "search", "--label", "team=a", "--label", "team=b"}, nil)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.ErrorContains(t, err, "more than once with different values")
}
