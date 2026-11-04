// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"os"
	"path/filepath"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestSandboxStop_routeAndWait(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "stop", "11111111-1111-1111-1111-111111111111"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "macos", "running")},
		{method: http.MethodPost, path: "/sandboxes/11111111-1111-1111-1111-111111111111/stop", status: 202, response: `{"status":"stopping","id":"11111111-1111-1111-1111-111111111111"}`},
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", query: "wait_exact=stopped&timeout_ms=55000", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "macos", "stopped")},
	})
	require.NoError(t, err)
	require.Equal(t, "stopped", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxStop_noWait(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "stop", "11111111-1111-1111-1111-111111111111", "--no-wait"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "macos", "running")},
		{method: http.MethodPost, path: "/sandboxes/11111111-1111-1111-1111-111111111111/stop", status: 202, response: `{"status":"stopping","id":"11111111-1111-1111-1111-111111111111"}`},
	})
	require.NoError(t, err)
	require.NotEmpty(t, requireJSONStdout(t, stdout))
}

func TestSandboxStop_invalidTimeoutFailsBeforeLookup(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	_, errb, code := run(t, d, nil,
		"--api-url", f.srv.URL,
		"--api-key", "k",
		"--project", testProjP1,
		"--timeout", "bad",
		"sandbox", "stop", "11111111-1111-1111-1111-111111111111",
	)
	require.Equal(t, cerr.ExitUsage, code)
	require.Contains(t, errb, "invalid --timeout")
	require.Empty(t, f.requests)
}

func TestSandboxPause_linuxRouteAndWait(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "pause", "11111111-1111-1111-1111-111111111111"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
		{method: http.MethodPost, path: "/sandboxes/11111111-1111-1111-1111-111111111111/pause", status: 202, response: `{"status":"pausing","id":"11111111-1111-1111-1111-111111111111"}`},
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", query: "wait=paused&timeout_ms=55000", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "paused")},
	})
	require.NoError(t, err)
	require.Equal(t, "paused", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxResume_linuxRouteAndWait(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "resume", "11111111-1111-1111-1111-111111111111"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "paused")},
		{method: http.MethodPost, path: "/sandboxes/11111111-1111-1111-1111-111111111111/resume", status: 202, response: `{"status":"pending","id":"11111111-1111-1111-1111-111111111111"}`},
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", query: "wait=running&timeout_ms=55000", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
	})
	require.NoError(t, err)
	require.Equal(t, "running", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxFork_waitsForRunningWhenSourceIsRunning(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "fork", "11111111-1111-1111-1111-111111111111", "--name", "forked"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
		{method: http.MethodPost, path: "/sandboxes/11111111-1111-1111-1111-111111111111/fork", status: 202, bodyJSON: `{"name":"forked"}`, response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","forked_from":"11111111-1111-1111-1111-111111111111","status":"pending","id":"22222222-2222-2222-2222-222222222222"}`},
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", query: "wait=running&timeout_ms=55000", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "linux", "running")},
	})
	require.NoError(t, err)
	require.Equal(t, "running", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxFork_noWaitHumanOutputShowsForkedID(t *testing.T) {
	stdout, stderr, err := runCLIText(t, []string{"sandbox", "fork", "11111111-1111-1111-1111-111111111111", "--no-wait"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "paused")},
		{method: http.MethodPost, path: "/sandboxes/11111111-1111-1111-1111-111111111111/fork", status: 202, response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","forked_from":"11111111-1111-1111-1111-111111111111","status":"pending","id":"22222222-2222-2222-2222-222222222222"}`},
	})
	require.NoError(t, err, stderr)
	require.Contains(t, stdout, "22222222-2222-2222-2222-222222222222")
	require.NotContains(t, stdout, "11111111-1111-1111-1111-111111111111")
}

func TestSandboxFork_rejectsUnknownClass(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "fork", "22222222-2222-2222-2222-222222222222"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "unknown-class", "paused")},
	})
	require.Error(t, err)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
}

func TestSandboxDelete_requiresYesInNonInteractive(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "delete", "11111111-1111-1111-1111-111111111111"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
	})
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestSandboxStart_macosRouteNoWait(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "start", "22222222-2222-2222-2222-222222222222", "--no-wait"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "macos", "stopped")},
		{method: http.MethodPost, path: "/sandboxes/22222222-2222-2222-2222-222222222222/start", status: 202, response: `{"status":"pending","id":"22222222-2222-2222-2222-222222222222"}`},
	})
	require.NoError(t, err)
	require.NotEmpty(t, requireJSONStdout(t, stdout))
}

func TestSandboxStart_waitAllowsInitialStoppedRace(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "start", "11111111-1111-1111-1111-111111111111"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "macos", "stopped")},
		{method: http.MethodPost, path: "/sandboxes/11111111-1111-1111-1111-111111111111/start", status: 202, response: `{"status":"pending","id":"11111111-1111-1111-1111-111111111111"}`},
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", query: "wait=running&timeout_ms=55000", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "macos", "stopped")},
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "macos", "running"), check: func(t *testing.T, r *http.Request, _ []byte) {
			// check runs on the handler goroutine: assert, never require.
			assert.Equal(t, "running", r.URL.Query().Get("wait"))
			assert.NotEmpty(t, r.URL.Query().Get("timeout_ms"))
		}},
	})
	require.NoError(t, err)
	require.Equal(t, "running", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxWait_runningConflictsImmediatelyWhenSandboxStopped(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "wait", "22222222-2222-2222-2222-222222222222", "--for", "running", "--timeout", "1s"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "macos", "stopped")},
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", query: "wait=running&timeout_ms=1000", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "macos", "stopped")},
	})
	require.Error(t, err)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
}

func TestSandboxStop_macosRouteNoWait(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "stop", "22222222-2222-2222-2222-222222222222", "--no-wait"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "macos", "running")},
		{method: http.MethodPost, path: "/sandboxes/22222222-2222-2222-2222-222222222222/stop", status: 202, response: `{"status":"stopping","id":"22222222-2222-2222-2222-222222222222"}`},
	})
	require.NoError(t, err)
	require.NotEmpty(t, requireJSONStdout(t, stdout))
}

func TestSandboxDelete_linuxRouteNoWait(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "delete", "22222222-2222-2222-2222-222222222222", "--yes", "--no-wait"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "linux", "running")},
		{method: http.MethodDelete, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 202, response: `{"status":"deleting","id":"22222222-2222-2222-2222-222222222222"}`},
	})
	require.NoError(t, err)
	require.NotEmpty(t, requireJSONStdout(t, stdout))
}

func TestSandboxUpdate_nothingToUpdate(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "update", "11111111-1111-1111-1111-111111111111"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
	})
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestSandboxUpdate_clearLabels(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "update", "11111111-1111-1111-1111-111111111111", "--clear-labels"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
		{method: http.MethodPut, path: "/sandboxes/11111111-1111-1111-1111-111111111111/labels", status: 200, bodyJSON: `{"labels":{}}`, response: `{"id":"11111111-1111-1111-1111-111111111111","labels":{}}`},
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
	})
	require.NoError(t, err)
	require.NotEmpty(t, requireJSONStdout(t, stdout))
}

func TestSandboxUpdate_lifecycleOnly(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "update", "11111111-1111-1111-1111-111111111111", "--idle-timeout", "120", "--on-idle-timeout", "pause"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
		{method: http.MethodPatch, path: "/sandboxes/11111111-1111-1111-1111-111111111111/lifecycle-policy", status: 200, bodyJSON: `{"idle_expiry_seconds":120,"on_idle_timeout":"pause"}`, response: sandboxLifecycleFixture("11111111-1111-1111-1111-111111111111", 120, "pause")},
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
	})
	require.NoError(t, err)
	require.NotEmpty(t, requireJSONStdout(t, stdout))
}

func TestSandboxUpdate_rejectsMacOSPauseLifecycleBeforePatch(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/sandboxes/22222222-2222-2222-2222-222222222222", 200, mustJSON(t, sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "macos", "running")))
	d := cfgDir(t)
	_, errb, code := run(t, d, nil,
		"--api-url", f.srv.URL,
		"--api-key", "k",
		"--project", testProjP1,
		"sandbox", "update", "22222222-2222-2222-2222-222222222222", "--idle-timeout", "120", "--on-idle-timeout", "pause",
	)
	require.Equal(t, cerr.ExitUsage, code)
	require.Contains(t, errb, "macos")
	require.Len(t, f.requests, 1)
	require.Equal(t, "/sandboxes/22222222-2222-2222-2222-222222222222", f.requests[0].Path)
}

func TestSandboxUpdate_buildsAndValidatesBothBodiesBeforeMutating(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/sandboxes/22222222-2222-2222-2222-222222222222", 200, mustJSON(t, sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "macos", "running")))
	_, errb, code := run(t, cfgDir(t), nil,
		"--api-url", f.srv.URL,
		"--api-key", "k",
		"--project", testProjP1,
		"sandbox", "update", "22222222-2222-2222-2222-222222222222", "--label", "env=dev", "--idle-timeout", "120", "--on-idle-timeout", "pause",
	)
	require.Equal(t, cerr.ExitUsage, code)
	require.Contains(t, errb, "macos")
	require.Len(t, f.requests, 1)
	require.Equal(t, "/sandboxes/22222222-2222-2222-2222-222222222222", f.requests[0].Path)
}

func TestSandboxUpdate_rejectsExplicitNullLabelsWithoutOverride(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "update.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"labels":null}`), 0o600))
	_, _, err := runCLI(t, []string{"sandbox", "update", "11111111-1111-1111-1111-111111111111", "--file", bodyPath}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
	})
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestSandboxUpdate_rejectsNullLifecycleWithoutOverride(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "update.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"lifecycle_policy":null}`), 0o600))
	_, _, err := runCLI(t, []string{"sandbox", "update", "11111111-1111-1111-1111-111111111111", "--file", bodyPath}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
	})
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestSandboxUpdate_lifecycleFlagsOverrideInvalidFileLifecycle(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "update.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"lifecycle_policy":null}`), 0o600))
	stdout, _, err := runCLI(t, []string{"sandbox", "update", "11111111-1111-1111-1111-111111111111", "--file", bodyPath, "--idle-timeout", "60", "--on-idle-timeout", "delete"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
		{method: http.MethodPatch, path: "/sandboxes/11111111-1111-1111-1111-111111111111/lifecycle-policy", status: 200, bodyJSON: `{"idle_expiry_seconds":60,"on_idle_timeout":"delete"}`, response: sandboxLifecycleFixture("11111111-1111-1111-1111-111111111111", 60, "delete")},
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
	})
	require.NoError(t, err)
	require.NotEmpty(t, requireJSONStdout(t, stdout))
}

func TestSandboxUpdate_rejectsNonObjectLifecycleWithoutOverride(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "update.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"lifecycle_policy":"never"}`), 0o600))
	_, _, err := runCLI(t, []string{"sandbox", "update", "11111111-1111-1111-1111-111111111111", "--file", bodyPath}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
	})
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

// macOS sandboxes take the lifecycle verbs the API and both SDKs give them: start, stop, pause,
// resume and delete, on the same /sandboxes routes as Linux sandboxes.
const testMacSandboxID = "44444444-4444-4444-4444-444444444444"

func TestSandboxStop_macosRouteAndWait(t *testing.T) {
	id := testMacSandboxID
	stdout, _, err := runCLI(t, []string{"sandbox", "stop", id}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "macos", "running")},
		{method: http.MethodPost, path: "/sandboxes/" + id + "/stop", status: 202, response: `{"status":"stopping","id":"` + id + `"}`},
		{method: http.MethodGet, path: "/sandboxes/" + id, query: "wait_exact=stopped&timeout_ms=55000", status: 200, response: sandboxDetailFixture(id, "macos", "stopped")},
	})
	require.NoError(t, err)
	require.Equal(t, "stopped", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxStart_macosRouteAndWait(t *testing.T) {
	id := testMacSandboxID
	stdout, _, err := runCLI(t, []string{"sandbox", "start", id}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "macos", "stopped")},
		{method: http.MethodPost, path: "/sandboxes/" + id + "/start", status: 202, response: `{"status":"pending","id":"` + id + `"}`},
		{method: http.MethodGet, path: "/sandboxes/" + id, query: "wait=running&timeout_ms=55000", status: 200, response: sandboxDetailFixture(id, "macos", "running")},
	})
	require.NoError(t, err)
	require.Equal(t, "running", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxDelete_macosRoute(t *testing.T) {
	id := testMacSandboxID
	stdout, _, err := runCLI(t, []string{"sandbox", "delete", id, "--yes", "--no-wait"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "macos", "stopped")},
		{method: http.MethodDelete, path: "/sandboxes/" + id, status: 202, response: `{"status":"deleting","id":"` + id + `"}`},
	})
	require.NoError(t, err)
	require.NotEmpty(t, requireJSONStdout(t, stdout))
}

func TestSandboxWait_macos(t *testing.T) {
	id := testMacSandboxID
	stdout, _, err := runCLI(t, []string{"sandbox", "wait", id, "--for", "stopped"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "macos", "stopping")},
		{method: http.MethodGet, path: "/sandboxes/" + id, query: "wait_exact=stopped&timeout_ms=55000", status: 200, response: sandboxDetailFixture(id, "macos", "stopped")},
	})
	require.NoError(t, err)
	require.Equal(t, "stopped", requireJSONStdout(t, stdout)["status"])
}

// The API holds `?wait=paused` until the sandbox is paused or the slice ends: it never answers early with pausing or running. A save longer than one slice
// answers pausing at the slice's end, and the next slice answers paused.
func TestSandboxPause_macosWaitsForPaused(t *testing.T) {
	id := testMacSandboxID
	stdout, _, err := runCLI(t, []string{"sandbox", "pause", id}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "macos", "running")},
		{method: http.MethodPost, path: "/sandboxes/" + id + "/pause", status: 202, response: `{"status":"pausing","id":"` + id + `"}`},
		{method: http.MethodGet, path: "/sandboxes/" + id, query: "wait=paused&timeout_ms=55000", status: 200, response: sandboxDetailFixture(id, "macos", "pausing")},
		{method: http.MethodGet, path: "/sandboxes/" + id, query: "wait=paused&timeout_ms=55000", status: 200, response: sandboxDetailFixture(id, "macos", "paused")},
	})
	require.NoError(t, err)
	require.Equal(t, "paused", requireJSONStdout(t, stdout)["status"])
}

// If a macOS pause returns to running, report the failed pause without waiting out --timeout.
func TestSandboxPause_macosConflictsWhenTheSandboxReturnsToRunning(t *testing.T) {
	id := testMacSandboxID
	_, _, err := runCLI(t, []string{"sandbox", "pause", id}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "macos", "running")},
		{method: http.MethodPost, path: "/sandboxes/" + id + "/pause", status: 202, response: `{"status":"pausing","id":"` + id + `"}`},
		{method: http.MethodGet, path: "/sandboxes/" + id, query: "wait=paused&timeout_ms=55000", status: 200, response: sandboxDetailFixture(id, "macos", "running")},
	})
	require.Error(t, err)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
	require.ErrorContains(t, err, "sandbox "+id+" reached running, not paused")
}

func TestSandboxResume_macosWaitsForRunning(t *testing.T) {
	id := testMacSandboxID
	stdout, _, err := runCLI(t, []string{"sandbox", "resume", id}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "macos", "paused")},
		{method: http.MethodPost, path: "/sandboxes/" + id + "/resume", status: 202, response: `{"status":"pending","id":"` + id + `"}`},
		{method: http.MethodGet, path: "/sandboxes/" + id, query: "wait=running&timeout_ms=55000", status: 200, response: sandboxDetailFixture(id, "macos", "running")},
	})
	require.NoError(t, err)
	require.Equal(t, "running", requireJSONStdout(t, stdout)["status"])
}

// A resume the Mac cannot finish ends stopped, as the SDKs' macOS resume reports it.
func TestSandboxResume_macosConflictsWhenTheSandboxEndsStopped(t *testing.T) {
	id := testMacSandboxID
	_, _, err := runCLI(t, []string{"sandbox", "resume", id}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "macos", "paused")},
		{method: http.MethodPost, path: "/sandboxes/" + id + "/resume", status: 202, response: `{"status":"pending","id":"` + id + `"}`},
		{method: http.MethodGet, path: "/sandboxes/" + id, query: "wait=running&timeout_ms=55000", status: 200, response: sandboxDetailFixture(id, "macos", "stopped")},
	})
	require.Error(t, err)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
	require.ErrorContains(t, err, "sandbox "+id+" reached stopped, not running")
}

// A macOS fork starts running only if its source is running when the fork is
// created. The source can change after our lookup, so the CLI accepts either
// a running or stopped fork, as the SDKs do. The fork's wait
// long-polls `?wait=running`, which the API also answers on stopping/stopped.
func runMacFork(t *testing.T, sampled string, forkAnswers ...string) (string, error) {
	t.Helper()
	id := testMacSandboxID
	fork := "22222222-2222-2222-2222-222222222222"
	expected := []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "macos", sampled)},
		{method: http.MethodPost, path: "/sandboxes/" + id + "/fork", status: 202, bodyJSON: `{"name":"copy"}`, response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","forked_from":"` + id + `","status":"pending","id":"` + fork + `"}`},
	}
	for i, status := range forkAnswers {
		query := ""
		if i == 0 {
			query = "wait=running&timeout_ms=55000"
		}
		expected = append(expected, expectedRequest{method: http.MethodGet, path: "/sandboxes/" + fork, query: query, status: 200, response: sandboxDetailFixture(fork, "macos", status)})
	}
	stdout, _, err := runCLI(t, []string{"sandbox", "fork", id, "--name", "copy"}, expected)
	return stdout, err
}

func TestSandboxFork_macosStoppedSourceSettlesStopped(t *testing.T) {
	stdout, err := runMacFork(t, "stopped", "stopped")
	require.NoError(t, err)
	require.Equal(t, "stopped", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxFork_macosRunningSourceSettlesRunning(t *testing.T) {
	stdout, err := runMacFork(t, "running", "running")
	require.NoError(t, err)
	require.Equal(t, "running", requireJSONStdout(t, stdout)["status"])
}

// The source stopped after the CLI's lookup but before the fork was created.
func TestSandboxFork_macosSampledRunningForkLandsStopped(t *testing.T) {
	stdout, err := runMacFork(t, "running", "stopped")
	require.NoError(t, err)
	require.Equal(t, "stopped", requireJSONStdout(t, stdout)["status"])
}

// The source started after the CLI's lookup but before the fork was created.
func TestSandboxFork_macosSampledStoppedForkLandsRunning(t *testing.T) {
	stdout, err := runMacFork(t, "stopped", "running")
	require.NoError(t, err)
	require.Equal(t, "running", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxFork_macosWaitsPastStopping(t *testing.T) {
	stdout, err := runMacFork(t, "paused", "stopping", "stopped")
	require.NoError(t, err)
	require.Equal(t, "stopped", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxFork_macosFailedForkConflicts(t *testing.T) {
	_, err := runMacFork(t, "running", "failed")
	require.Error(t, err)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
	require.ErrorContains(t, err, "reached failed, not running")
}
