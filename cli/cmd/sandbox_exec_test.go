// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestToolboxExec_propagatesExitCode(t *testing.T) {
	stdinFile, openErr := os.CreateTemp(t.TempDir(), "stdin-*.txt")
	require.NoError(t, openErr)
	require.NoError(t, stdinFile.Close())
	stdinFile, openErr = os.Open(stdinFile.Name())
	require.NoError(t, openErr)
	defer stdinFile.Close()
	oldStdin := os.Stdin
	os.Stdin = stdinFile
	defer func() { os.Stdin = oldStdin }()

	stdout, _, err := runToolboxCLI(t, []string{"sandbox", "exec", "sbx", "--", "sh", "-c", "exit 7"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodPost, path: "/test-tools/sbx/processes", status: 201, auth: true, query: url.Values{"logs": {"base64"}, "waitMs": {"1000"}}, response: `{"id":"proc","state":"terminal","kind":"exec","stdin":"closed","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","reason":"exited","exitCode":7,"logs":{"frames":[{"channel":"stdout","cursor":"c1","data":"aGVsbG8K","timestamp":1}],"nextCursor":"c1","truncatedHead":false,"eof":true}}`, check: func(t *testing.T, _ *http.Request, body []byte) {
			var payload map[string]any
			assert.NoError(t, json.Unmarshal(body, &payload))
			assert.Equal(t, []any{"sh", "-c", "exit 7"}, payload["argv"])
			assert.Equal(t, "open", payload["stdin"])
		}},
	})
	require.Equal(t, 7, cerr.ExitCode(err))
	require.Equal(t, "hello\n", stdout)
}

// `reason` is optional on the wire: a nonzero exitCode without a known one
// still fails the command with that code.
func TestExitError_exitCodeWithoutKnownReason(t *testing.T) {
	code := int32(7)
	unknown := tbgen.Reason("future_reason")
	require.Equal(t, 7, cerr.ExitCode(exitError(tbgen.ExitResult{ExitCode: &code})))
	require.Equal(t, 7, cerr.ExitCode(exitError(tbgen.ExitResult{ExitCode: &code, Reason: &unknown})))
	zero := int32(0)
	require.NoError(t, exitError(tbgen.ExitResult{ExitCode: &zero}))
	require.NoError(t, exitError(tbgen.ExitResult{}))
}

func TestToolboxExec_forwardsRegularFileStdin(t *testing.T) {
	file, err := os.CreateTemp(t.TempDir(), "stdin-*.txt")
	require.NoError(t, err)
	_, err = file.WriteString("hello")
	require.NoError(t, err)
	require.NoError(t, file.Close())
	file, err = os.Open(file.Name())
	require.NoError(t, err)
	defer file.Close()
	oldStdin := os.Stdin
	os.Stdin = file
	defer func() { os.Stdin = oldStdin }()

	// The process response reports `cat` as RUNNING after the inline wait
	// (it is waiting for stdin), the CLI pumps data + EOF, then follows the
	// log stream to the exit.
	var stdinPosts []map[string]any
	var mu sync.Mutex
	serverURL := ""
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/sbx":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, strings.ReplaceAll(sandboxDetailFixture("sbx", "linux", "running"), "https://toolbox.example", serverURL+"/test-tools"))
		case "/test-tools/sbx/processes":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"id":"proc","state":"running","kind":"exec","stdin":"open","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","logs":{"frames":[],"nextCursor":"c0","truncatedHead":false,"eof":false}}`)
		case "/test-tools/sbx/processes/proc/stdin":
			var payload map[string]any
			body, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(body, &payload)
			mu.Lock()
			stdinPosts = append(stdinPosts, payload)
			mu.Unlock()
			w.WriteHeader(http.StatusNoContent)
		case "/test-tools/sbx/processes/proc/logs":
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = io.WriteString(w, "event: log\nid: c1\ndata: {\"channel\":\"stdout\",\"data\":\"aGVsbG8=\",\"cursor\":\"c1\"}\n\n")
			_, _ = io.WriteString(w, "event: state\ndata: {\"reason\":\"exited\",\"exitCode\":0}\n\n")
			_, _ = io.WriteString(w, "event: eof\ndata: {}\n\n")
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.String())
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	serverURL = server.URL
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", server.URL, "--api-key", "k", "sandbox", "exec", "sbx", "--", "cat")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, "hello", stdout)
	mu.Lock()
	defer mu.Unlock()
	require.Len(t, stdinPosts, 2)
	require.Equal(t, "aGVsbG8=", stdinPosts[0]["data"])
	require.Equal(t, false, stdinPosts[0]["eof"])
	require.Equal(t, true, stdinPosts[1]["eof"])
}

// A one-shot command exits within the inline wait,
// so the create response is already TERMINAL. With stdin redirected (the
// agent/CI case) the CLI must not pump stdin into a finished process — the
// API would answer 409 PROCESS_TERMINAL and mask the real exit code.
func TestToolboxExec_terminalAtCreateSkipsStdinAndKeepsExitCode(t *testing.T) {
	file, err := os.CreateTemp(t.TempDir(), "stdin-*.txt")
	require.NoError(t, err)
	require.NoError(t, file.Close())
	file, err = os.Open(file.Name())
	require.NoError(t, err)
	defer file.Close()
	oldStdin := os.Stdin
	os.Stdin = file
	defer func() { os.Stdin = oldStdin }()

	serverURL := ""
	var stdinCalls int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/sbx":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, strings.ReplaceAll(sandboxDetailFixture("sbx", "linux", "running"), "https://toolbox.example", serverURL+"/test-tools"))
		case "/test-tools/sbx/processes":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"id":"proc","state":"terminal","kind":"exec","stdin":"open","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","reason":"exited","exitCode":7,"logs":{"frames":[{"channel":"stdout","cursor":"c1","data":"aGkK","timestamp":1}],"nextCursor":"c1","truncatedHead":false,"eof":true}}`)
		case "/test-tools/sbx/processes/proc/stdin":
			atomic.AddInt32(&stdinCalls, 1)
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusConflict)
			_, _ = io.WriteString(w, `{"code":"PROCESS_TERMINAL","message":"process proc already exited","source":"DAYTONA_DAEMON"}`)
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.String())
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	serverURL = server.URL
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", server.URL, "--api-key", "k", "sandbox", "exec", "sbx", "--", "sh", "-c", "echo hi; exit 7")
	require.Equal(t, 7, code, stderr)
	require.Equal(t, "hi\n", stdout)
	require.Equal(t, int32(0), atomic.LoadInt32(&stdinCalls), "no stdin write against a terminal process")
}

// The race the guard above cannot close: the process exits BETWEEN the create
// response (running) and the stdin write. A 409 PROCESS_TERMINAL
// is end-of-input, not a failure — the exit code is the command's.
func TestToolboxExec_lateStdinTerminalIsBenign(t *testing.T) {
	file, err := os.CreateTemp(t.TempDir(), "stdin-*.txt")
	require.NoError(t, err)
	_, _ = file.WriteString("ignored")
	require.NoError(t, file.Close())
	file, err = os.Open(file.Name())
	require.NoError(t, err)
	defer file.Close()
	oldStdin := os.Stdin
	os.Stdin = file
	defer func() { os.Stdin = oldStdin }()

	serverURL := ""
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/sbx":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, strings.ReplaceAll(sandboxDetailFixture("sbx", "linux", "running"), "https://toolbox.example", serverURL+"/test-tools"))
		case "/test-tools/sbx/processes":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"id":"proc","state":"running","kind":"exec","stdin":"open","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","logs":{"frames":[],"nextCursor":"c0","truncatedHead":false,"eof":false}}`)
		case "/test-tools/sbx/processes/proc/stdin":
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusConflict)
			_, _ = io.WriteString(w, `{"code":"PROCESS_TERMINAL","message":"process proc already exited","source":"DAYTONA_DAEMON"}`)
		case "/test-tools/sbx/processes/proc/logs":
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = io.WriteString(w, "event: state\ndata: {\"reason\":\"exited\",\"exitCode\":3}\n\n")
			_, _ = io.WriteString(w, "event: eof\ndata: {}\n\n")
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.String())
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	serverURL = server.URL
	_, stderr, code := run(t, cfgDir(t), nil, "--api-url", server.URL, "--api-key", "k", "sandbox", "exec", "sbx", "--", "true")
	require.Equal(t, 3, code, stderr)
}
