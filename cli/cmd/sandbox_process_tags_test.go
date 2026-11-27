// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/json"
	"net/http"
	"net/url"
	"os"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/daytona/clients/cli/internal/cerr"
)

func TestProcessListFiltersByTag(t *testing.T) {
	stdout, _, err := runToolboxCLI(t, []string{"sandbox", "process", "list", "sbx", "--tag", "build.1"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodGet, path: "/test-tools/sbx/processes", status: 200, auth: true, query: url.Values{"tag": {"build.1"}}, response: `{"items":[]}`},
	})
	require.NoError(t, err)
	require.Empty(t, requireJSONStdout(t, stdout)["items"])
}

func TestProcessKillAllKillsTheTag(t *testing.T) {
	stdout, _, err := runToolboxCLI(t, []string{"sandbox", "process", "kill-all", "sbx", "--tag", "build", "--timeout", "1500us"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodPost, path: "/test-tools/sbx/processes:kill", status: 200, auth: true, response: `{"processIds":["p1","p2"]}`, check: func(t *testing.T, _ *http.Request, body []byte) {
			var payload map[string]any
			assert.NoError(t, json.Unmarshal(body, &payload))
			// 1.5ms rounds up, never down to a zero (invalid) wait.
			assert.Equal(t, map[string]any{"tag": "build", "timeoutMs": float64(2)}, payload)
		}},
	})
	require.NoError(t, err)
	require.Equal(t, []any{"p1", "p2"}, requireJSONStdout(t, stdout)["process_ids"])
}

func TestProcessKillAllWithoutTimeoutKeepsTheToolboxDefault(t *testing.T) {
	_, _, err := runToolboxCLI(t, []string{"sandbox", "process", "kill-all", "sbx", "--tag", "build"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodPost, path: "/test-tools/sbx/processes:kill", status: 200, auth: true, response: `{"processIds":[]}`, check: func(t *testing.T, _ *http.Request, body []byte) {
			assert.JSONEq(t, `{"tag":"build"}`, string(body))
		}},
	})
	require.NoError(t, err)
}

func TestProcessTagIsCheckedBeforeAnyRequest(t *testing.T) {
	for _, args := range [][]string{
		{"sandbox", "process", "kill-all", "sbx"},
		{"sandbox", "process", "kill-all", "sbx", "--tag", ""},
		{"sandbox", "process", "kill-all", "sbx", "--tag", ".."},
		{"sandbox", "process", "kill-all", "sbx", "--tag", "a b"},
		{"sandbox", "process", "kill-all", "sbx", "--tag", "ok", "--timeout", "61s"},
		{"sandbox", "process", "kill-all", "sbx", "--tag", "ok", "--timeout", "0s"},
		{"sandbox", "process", "list", "sbx", "--tag", "a/b"},
		{"sandbox", "exec", "sbx", "--tag", "x y", "--", "true"},
		{"sandbox", "exec", "sbx", "--name", " ", "--", "true"},
	} {
		_, _, err := runToolboxCLI(t, args, nil)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), "%v: %v", args, err)
	}
}

func TestExecSendsNameAndTag(t *testing.T) {
	stdinFile, err := os.CreateTemp(t.TempDir(), "stdin-*.txt")
	require.NoError(t, err)
	require.NoError(t, stdinFile.Close())
	stdinFile, err = os.Open(stdinFile.Name())
	require.NoError(t, err)
	defer stdinFile.Close()
	oldStdin := os.Stdin
	os.Stdin = stdinFile
	defer func() { os.Stdin = oldStdin }()

	_, _, err = runToolboxCLI(t, []string{"sandbox", "exec", "sbx", "--name", "web", "--tag", "build", "--", "true"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodPost, path: "/test-tools/sbx/processes", status: 201, auth: true, query: url.Values{"logs": {"base64"}, "waitMs": {"1000"}}, response: `{"id":"proc","state":"terminal","kind":"exec","stdin":"closed","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","reason":"exited","exitCode":0,"logs":{"frames":[],"nextCursor":"","truncatedHead":false,"eof":true}}`, check: func(t *testing.T, _ *http.Request, body []byte) {
			var payload map[string]any
			assert.NoError(t, json.Unmarshal(body, &payload))
			assert.Equal(t, "web", payload["name"])
			assert.Equal(t, "build", payload["tag"])
		}},
	})
	require.NoError(t, err)
}
