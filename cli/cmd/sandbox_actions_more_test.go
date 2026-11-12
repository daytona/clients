// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	gen "github.com/daytona/clients/cli/gen/api"
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
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "macos", "stopping")},
	})
	require.NoError(t, err)
	require.JSONEq(t, sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "macos", "stopping"), stdout)
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
	require.Contains(t, errb, `invalid argument "bad" for "--timeout" flag`)
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
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "linux", "pending")},
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
		macReservationsRead("region-b", "active"),
		{method: http.MethodPost, path: "/sandboxes/22222222-2222-2222-2222-222222222222/start", status: 202, response: `{"status":"pending","id":"22222222-2222-2222-2222-222222222222"}`},
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "macos", "pending")},
	})
	require.NoError(t, err)
	require.JSONEq(t, sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "macos", "pending"), stdout)
}

func TestSandboxStart_waitAllowsInitialStoppedRace(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "start", "11111111-1111-1111-1111-111111111111"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "macos", "stopped")},
		macReservationsRead("region-b", "active"),
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
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "macos", "stopping")},
	})
	require.NoError(t, err)
	require.JSONEq(t, sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "macos", "stopping"), stdout)
}

func TestSandboxDelete_linuxRouteNoWait(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "delete", "22222222-2222-2222-2222-222222222222", "--yes", "--no-wait"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "linux", "running")},
		{method: http.MethodDelete, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 202, response: `{"status":"deleting","id":"22222222-2222-2222-2222-222222222222"}`},
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "linux", "deleting")},
	})
	require.NoError(t, err)
	require.JSONEq(t, sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "linux", "deleting"), stdout)
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
	stdout, _, err := runCLI(t, []string{"sandbox", "update", "11111111-1111-1111-1111-111111111111", "--idle-timeout", "2m", "--on-idle-timeout", "delete", "--lifecycle-timeout", "1h30m"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
		{method: http.MethodPatch, path: "/sandboxes/11111111-1111-1111-1111-111111111111/lifecycle-policy", status: 200, bodyJSON: `{"idle_expiry_seconds":120,"on_idle_timeout":"delete","timeout_seconds":5400}`, response: sandboxLifecycleFixture("11111111-1111-1111-1111-111111111111", 120, "delete")},
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
		"sandbox", "update", "22222222-2222-2222-2222-222222222222", "--idle-timeout", "2m", "--on-idle-timeout", "pause",
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
		"sandbox", "update", "22222222-2222-2222-2222-222222222222", "--label", "env=dev", "--idle-timeout", "2m", "--on-idle-timeout", "pause",
	)
	require.Equal(t, cerr.ExitUsage, code)
	require.Contains(t, errb, "macos")
	require.Len(t, f.requests, 1)
	require.Equal(t, "/sandboxes/22222222-2222-2222-2222-222222222222", f.requests[0].Path)
}

// The lifecycle actions follow the SDKs: delete everywhere, stop only on a
// persistent macOS sandbox, never pause. A refused action fails before any
// write.
func TestSandboxUpdate_lifecycleActionMatrix(t *testing.T) {
	cases := []struct {
		name, class, action string
		persistent          bool
		ok                  bool
	}{
		{"linux delete", "linux", "delete", false, true},
		{"linux stop", "linux", "stop", false, false},
		{"linux pause", "linux", "pause", false, false},
		{"ephemeral macos stop", "macos", "stop", false, false},
		{"persistent macos stop", "macos", "stop", true, true},
		{"persistent macos pause", "macos", "pause", true, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			detail := mustJSON(t, sandboxDetailFixture("22222222-2222-2222-2222-222222222222", tc.class, "running"))
			detail["persistent"] = tc.persistent
			f := newFakeAPI(t)
			f.on("GET", "/sandboxes/22222222-2222-2222-2222-222222222222", 200, detail)
			f.on("PATCH", "/sandboxes/22222222-2222-2222-2222-222222222222/lifecycle-policy", 200, mustJSON(t, sandboxLifecycleFixture("22222222-2222-2222-2222-222222222222", 120, gen.LifecycleAction(tc.action))))
			_, errb, code := run(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "k",
				"sandbox", "update", "22222222-2222-2222-2222-222222222222", "--lifecycle-timeout", "2m", "--on-lifecycle-timeout", tc.action)
			if tc.ok {
				require.Equal(t, 0, code, errb)
				require.Equal(t, map[string]any{"timeout_seconds": float64(120), "on_timeout": tc.action}, findRequest(t, f, "PATCH", "/sandboxes/22222222-2222-2222-2222-222222222222/lifecycle-policy").Body)
				return
			}
			require.Equal(t, cerr.ExitUsage, code, errb)
			require.Contains(t, errb, "--on-lifecycle-timeout")
			for _, r := range f.requests {
				require.Equal(t, "GET", r.Method, "no write after a refused action")
			}
		})
	}
}

func TestSandboxUpdate_idleTimeoutOffClearsTheIdleExpiry(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "update", "11111111-1111-1111-1111-111111111111", "--idle-timeout", "off"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
		{method: http.MethodPatch, path: "/sandboxes/11111111-1111-1111-1111-111111111111/lifecycle-policy", status: 200, bodyJSON: `{"idle_expiry_seconds":-1}`, response: sandboxLifecycleFixture("11111111-1111-1111-1111-111111111111", 120, "delete")},
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
	})
	require.NoError(t, err)
}

func TestSandboxUpdate_durationFlagsTakeWholeSecondGoDurations(t *testing.T) {
	for _, args := range [][]string{
		{"--idle-timeout", "120"},          // no unit
		{"--idle-timeout", "1500ms"},       // not whole seconds
		{"--lifecycle-timeout", "0s"},      // zero
		{"--lifecycle-timeout", "-5m"},     // negative
		{"--lifecycle-timeout", "off"},     // keyword not offered here
		{"--lifecycle-timeout", "800000h"}, // beyond the API's range
		{"--timeout", "30s"},               // the old lifecycle spelling is gone
		{"--on-timeout", "delete"},         // likewise
	} {
		_, _, err := runCLI(t, append([]string{"sandbox", "update", "11111111-1111-1111-1111-111111111111"}, args...), nil)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), args)
	}
}

// sandbox update --file is the CLI's composite of the two requests: only
// `labels` and `lifecycle_policy` are accepted, and anything else is refused
// before any request (a dropped key would silently skip a lifecycle change).
func TestSandboxUpdate_fileRejectsUnknownTopLevelKeys(t *testing.T) {
	for _, body := range []string{
		`{"labels":{"env":"prod"},"timeout_seconds":60,"on_timeout":"delete"}`,
		`{"Labels":{"env":"prod"}}`,
	} {
		bodyPath := filepath.Join(t.TempDir(), "update.json")
		require.NoError(t, os.WriteFile(bodyPath, []byte(body), 0o600))
		_, _, err := runCLI(t, []string{"sandbox", "update", "11111111-1111-1111-1111-111111111111", "--file", bodyPath}, nil)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), body)
		require.ErrorContains(t, err, "unknown field")
	}
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

// A field name that differs only in case is unknown, also when a flag sets
// that field.
func TestSandboxUpdate_caseVariantLifecycleFieldIsUnknown(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "update.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"lifecycle_policy":{"Idle_Expiry_Seconds":null,"on_idle_timeout":"delete"}}`), 0o600))
	// Nothing is sent: the body is checked before the sandbox is looked up.
	_, _, err := runCLI(t, []string{"sandbox", "update", "11111111-1111-1111-1111-111111111111", "--file", bodyPath, "--idle-timeout", "1m"}, nil)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.ErrorContains(t, err, "unknown field")
}

func TestSandboxUpdate_lifecycleFlagsOverrideInvalidFileLifecycle(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "update.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"lifecycle_policy":null}`), 0o600))
	stdout, _, err := runCLI(t, []string{"sandbox", "update", "11111111-1111-1111-1111-111111111111", "--file", bodyPath, "--idle-timeout", "1m", "--on-idle-timeout", "delete"}, []expectedRequest{
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
		macReservationsRead("region-b", "active"),
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
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 404, response: `{"code":"NOT_FOUND","message":"sandbox deleted","source":"DAYTONA_API"}`},
	})
	require.NoError(t, err)
	require.JSONEq(t, `{"status":"deleting","id":"`+id+`"}`, stdout)
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

// A macOS fork follows its source state: running sources wait through the
// stopped disk-preparation state until running; paused/stopped sources wait stopped.
func runMacFork(t *testing.T, sampled string, forkAnswers ...string) (string, error) {
	t.Helper()
	id := testMacSandboxID
	fork := "22222222-2222-2222-2222-222222222222"
	expected := []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "macos", sampled)},
		{method: http.MethodPost, path: "/sandboxes/" + id + "/fork", status: 202, bodyJSON: `{"name":"copy"}`, response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","forked_from":"` + id + `","status":"pending","id":"` + fork + `"}`},
	}
	query := "wait_exact=stopped&timeout_ms=55000"
	if sampled == "running" {
		query = "wait=running&timeout_ms=55000"
	}
	for _, status := range forkAnswers {
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

func TestSandboxFork_macosRunningSourceWaitsPastStopped(t *testing.T) {
	stdout, err := runMacFork(t, "running", "pending", "stopped", "stopped", "booting", "running")
	require.NoError(t, err)
	require.Equal(t, "running", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxFork_macosPausedSourceSettlesStopped(t *testing.T) {
	stdout, err := runMacFork(t, "paused", "stopped")
	require.NoError(t, err)
	require.Equal(t, "stopped", requireJSONStdout(t, stdout)["status"])
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

// Label keys are the user's names: keys that differ only in case are two
// labels, sent as given.
func TestSandboxUpdate_fileLabelsDifferingOnlyInCase(t *testing.T) {
	body := filepath.Join(t.TempDir(), "update.json")
	require.NoError(t, os.WriteFile(body, []byte(`{"labels":{"a":"1","A":"2"}}`), 0o600))
	_, _, err := runCLI(t, []string{"sandbox", "update", "11111111-1111-1111-1111-111111111111", "--file", body}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
		{method: http.MethodPut, path: "/sandboxes/11111111-1111-1111-1111-111111111111/labels", status: 200, bodyJSON: `{"labels":{"a":"1","A":"2"}}`, response: `{"id":"11111111-1111-1111-1111-111111111111","labels":{"a":"1","A":"2"}}`},
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
	})
	require.NoError(t, err)
}

// macReservationsRead answers the reservation read of a macOS start with one
// reservation in region, in status.
func macReservationsRead(region, status string) expectedRequest {
	reservation := strings.Replace(macReservationFixture(`"m4-10c-16g"`, m4Capacity), `"region_id":"region-a","status":"active"`, `"region_id":"`+region+`","status":"`+status+`"`, 1)
	return expectedRequest{method: http.MethodGet, path: "/reservations", query: "limit=200", status: 200, response: `{"reservations":[` + reservation + `]}`}
}

func TestSandboxStart_macosAsksBeforeAutoReserveCanBuy(t *testing.T) {
	id := testMacSandboxID
	stopped := expectedRequest{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "macos", "stopped")}
	for name, read := range map[string]expectedRequest{
		"another region": macReservationsRead("region-a", "active"),
		"only an ending": macReservationsRead("region-b", "ending"),
		"unreadable":     {method: http.MethodGet, path: "/reservations", query: "limit=200", status: 403, response: `{"code":"FORBIDDEN","message":"missing read:reservations","source":"DAYTONA_API"}`},
	} {
		t.Run(name, func(t *testing.T) {
			// Off a terminal without --yes: refused after the reservation read
			// and before any start request.
			readDone := false
			read.check = func(*testing.T, *http.Request, []byte) { readDone = true }
			_, _, err := runCLI(t, []string{"sandbox", "start", id, "--no-wait"}, []expectedRequest{stopped, read})
			require.True(t, readDone, "the reservations were not read before the refusal")
			require.Error(t, err)
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
			require.Contains(t, err.Error(), "24-hour Mac reservation in region-b")
		})
	}
}

func TestSandboxStart_macosYesSkipsTheReservationRead(t *testing.T) {
	id := testMacSandboxID
	_, _, err := runCLI(t, []string{"sandbox", "start", id, "--no-wait", "--yes"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "macos", "stopped")},
		{method: http.MethodPost, path: "/sandboxes/" + id + "/start", status: 202, response: `{"status":"pending","id":"` + id + `"}`},
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "macos", "pending")},
	})
	require.NoError(t, err)
}

func TestSandboxStart_linuxReadsNoReservations(t *testing.T) {
	id := "11111111-1111-1111-1111-111111111111"
	_, _, err := runCLI(t, []string{"sandbox", "start", id, "--no-wait"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "linux", "stopped")},
		{method: http.MethodPost, path: "/sandboxes/" + id + "/start", status: 202, response: `{"status":"pending","id":"` + id + `"}`},
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "linux", "pending")},
	})
	require.NoError(t, err)
}

func TestSandboxStart_macosFindsALiveReservationOnALaterPage(t *testing.T) {
	id := testMacSandboxID
	elsewhere := strings.Replace(macReservationFixture(`"m4-10c-16g"`, m4Capacity), `"region_id":"region-a"`, `"region_id":"region-c"`, 1)
	_, _, err := runCLI(t, []string{"sandbox", "start", id, "--no-wait"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "macos", "stopped")},
		{method: http.MethodGet, path: "/reservations", query: "limit=200", status: 200, response: `{"reservations":[` + elsewhere + `],"next_cursor":"page-2"}`},
		{method: http.MethodGet, path: "/reservations", query: "cursor=page-2&limit=200", status: 200, response: `{"reservations":[` + strings.Replace(macReservationFixture(`"m4-10c-16g"`, m4Capacity), `"region_id":"region-a"`, `"region_id":"region-b"`, 1) + `]}`},
		{method: http.MethodPost, path: "/sandboxes/" + id + "/start", status: 202, response: `{"status":"pending","id":"` + id + `"}`},
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "macos", "pending")},
	})
	require.NoError(t, err)
}

func TestSandboxStart_macosPendingReservationsAreLive(t *testing.T) {
	id := testMacSandboxID
	for _, status := range []string{"pending_charge", "pending_mac", "active"} {
		t.Run(status, func(t *testing.T) {
			_, _, err := runCLI(t, []string{"sandbox", "start", id, "--no-wait"}, []expectedRequest{
				{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "macos", "stopped")},
				macReservationsRead("region-b", status),
				{method: http.MethodPost, path: "/sandboxes/" + id + "/start", status: 202, response: `{"status":"pending","id":"` + id + `"}`},
				{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "macos", "pending")},
			})
			require.NoError(t, err)
		})
	}
}
