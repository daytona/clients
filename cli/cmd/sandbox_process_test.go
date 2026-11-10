// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestToolboxProcessCommands_routeAndRender(t *testing.T) {
	t.Run("list", func(t *testing.T) {
		stdout, _, err := runToolboxCLI(t, []string{"sandbox", "process", "list", "sbx", "--state", "running", "--kind", "exec", "--pid", "42"}, []toolboxExpectedRequest{
			{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
			{method: http.MethodGet, path: "/test-tools/sbx/processes", status: 200, auth: true, query: url.Values{"state": {"running"}, "kind": {"exec"}, "pid": {"42"}}, response: `{"items":[{"id":"p1","kind":"exec","state":"running","stdin":"closed","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z"}]}`},
		})
		require.NoError(t, err)
		items := requireJSONStdout(t, stdout)["items"].([]any)
		require.Len(t, items, 1)
		item := items[0].(map[string]any)
		// Toolbox output is converted to snake_case.
		require.Equal(t, "on_exit_ttl", item["keep_logs"])
		require.Equal(t, false, item["truncated_head"])
		require.Equal(t, "2026-09-13T00:00:00Z", item["created_at"])
		require.NotContains(t, item, "keepLogs")
	})

	t.Run("get renders key fields in table mode", func(t *testing.T) {
		stdout, _, err := runToolboxCLI(t, []string{"--output", "table", "sandbox", "process", "get", "sbx", "p1"}, []toolboxExpectedRequest{
			{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
			{method: http.MethodGet, path: "/test-tools/sbx/processes/p1", status: 200, auth: true, response: `{"id":"p1","kind":"exec","state":"terminal","stdin":"closed","cwd":"/work","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","exitCode":3,"reason":"exited"}`},
		})
		require.NoError(t, err)
		require.Contains(t, stdout, "id")
		require.Contains(t, stdout, "p1")
		require.Contains(t, stdout, "kind")
		require.Contains(t, stdout, "exec")
		require.Contains(t, stdout, "state")
		require.Contains(t, stdout, "terminal")
		require.Contains(t, stdout, "exit_code")
		require.Contains(t, stdout, "3")
		require.Contains(t, stdout, "exit_reason")
		require.Contains(t, stdout, "exited")
		require.Contains(t, stdout, "created_at")
		require.Contains(t, stdout, "cwd")
		require.Contains(t, stdout, "/work")
	})

	t.Run("wait propagates exit code", func(t *testing.T) {
		stdout, _, err := runToolboxCLI(t, []string{"sandbox", "process", "wait", "sbx", "p1", "--timeout", "1s"}, []toolboxExpectedRequest{
			{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
			{method: http.MethodGet, path: "/test-tools/sbx/processes/p1/wait", status: 200, auth: true, response: `{"reason":"exited","exitCode":9}`, check: func(t *testing.T, r *http.Request, _ []byte) {
				ms := parseInt64(t, r.URL.Query().Get("timeoutMs"))
				assert.Greater(t, ms, int64(0))
				assert.LessOrEqual(t, ms, int64(1000))
			}},
		})
		require.Equal(t, 9, cerr.ExitCode(err))
		require.Equal(t, float64(9), requireJSONStdout(t, stdout)["exit_code"])
	})

	t.Run("a process ended by SIGINT exits 130, by another signal 1", func(t *testing.T) {
		for signal, want := range map[string]int{"SIGINT": cerr.ExitInterrupted, "SIGTERM": 143, "SIGKILL": 137} {
			_, _, err := runToolboxCLI(t, []string{"sandbox", "process", "wait", "sbx", "p1", "--timeout", "1s"}, []toolboxExpectedRequest{
				{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
				{method: http.MethodGet, path: "/test-tools/sbx/processes/p1/wait", status: 200, auth: true, response: `{"reason":"signaled","signal":"` + signal + `"}`},
			})
			require.Equal(t, want, cerr.ExitCode(err), signal)
			require.Equal(t, cerr.SourceProcess, cerr.DetailsOf(err).Source, signal)
		}
	})

	t.Run("signal", func(t *testing.T) {
		_, _, err := runToolboxCLI(t, []string{"sandbox", "process", "signal", "sbx", "p1", "TERM", "--escalate-after", "5s", "--escalate-to", "KILL"}, []toolboxExpectedRequest{
			{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
			{method: http.MethodPost, path: "/test-tools/sbx/processes/p1/signals", status: 204, auth: true, check: func(t *testing.T, _ *http.Request, body []byte) {
				assert.JSONEq(t, `{"signal":"SIGTERM","escalateAfterMs":5000,"escalateTo":"SIGKILL"}`, string(body))
			}},
		})
		require.NoError(t, err)
	})

	t.Run("signal rejects non-positive escalation delay", func(t *testing.T) {
		for _, value := range []string{"0s", "-1s"} {
			_, _, err := runToolboxCLI(t, []string{"sandbox", "process", "signal", "sbx", "p1", "TERM", "--escalate-after", value, "--escalate-to", "KILL"}, nil)
			require.Error(t, err)
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
		}
	})

	t.Run("kill", func(t *testing.T) {
		_, _, err := runToolboxCLI(t, []string{"sandbox", "process", "kill", "sbx", "p1"}, []toolboxExpectedRequest{
			{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
			{method: http.MethodPost, path: "/test-tools/sbx/processes/p1/signals", status: 204, auth: true, check: func(t *testing.T, _ *http.Request, body []byte) {
				assert.JSONEq(t, `{"signal":"SIGKILL"}`, string(body))
			}},
		})
		require.NoError(t, err)
	})

	t.Run("logs", func(t *testing.T) {
		stdout, _, err := runToolboxCLI(t, []string{"--output", "table", "sandbox", "process", "logs", "sbx", "p1"}, []toolboxExpectedRequest{
			{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
			{method: http.MethodGet, path: "/test-tools/sbx/processes/p1/logs", status: 200, auth: true, query: url.Values{"encoding": {"text"}}, responseContentType: "text/event-stream", response: `{"frames":[{"channel":"stdout","cursor":"c1","data":"hello\n","timestamp":1}],"nextCursor":"c1","truncatedHead":false,"eof":true}`},
		})
		require.NoError(t, err)
		require.Equal(t, "hello\n", stdout)
	})

	t.Run("logs warns when retention truncated the head", func(t *testing.T) {
		_, stderr, err := runToolboxCLI(t, []string{"--output", "table", "sandbox", "process", "logs", "sbx", "p1"}, []toolboxExpectedRequest{
			{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
			{method: http.MethodGet, path: "/test-tools/sbx/processes/p1/logs", status: 200, auth: true, query: url.Values{"encoding": {"text"}}, responseContentType: "text/event-stream", response: `{"frames":[{"channel":"stdout","cursor":"c9","data":"tail\n","timestamp":1}],"nextCursor":"c9","truncatedHead":true,"firstAvailableCursor":"c5","eof":true}`},
		})
		require.NoError(t, err)
		require.Contains(t, stderr, "evicted by retention")
		require.Contains(t, stderr, "c5")
	})

	t.Run("logs rejects an unknown encoding before any request", func(t *testing.T) {
		_, _, err := runToolboxCLI(t, []string{"sandbox", "process", "logs", "sbx", "p1", "--encoding", "hex"}, nil)
		require.Error(t, err)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	})

	t.Run("logs sanitizes terminal control sequences in human mode", func(t *testing.T) {
		stdout, _, err := runToolboxCLI(t, []string{"--output", "table", "sandbox", "process", "logs", "sbx", "p1"}, []toolboxExpectedRequest{
			{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
			{method: http.MethodGet, path: "/test-tools/sbx/processes/p1/logs", status: 200, auth: true, query: url.Values{"encoding": {"text"}}, responseContentType: "text/event-stream", response: "{\"frames\":[{\"channel\":\"stdout\",\"cursor\":\"c1\",\"data\":\"hello\\u001b[31mred\\u001b[0m\\n\",\"timestamp\":1}],\"nextCursor\":\"c1\",\"truncatedHead\":false,\"eof\":true}"},
		})
		require.NoError(t, err)
		require.NotContains(t, stdout, "\x1b[")
		require.Equal(t, "hellored\n", stdout)
	})

	t.Run("follow propagates exit code", func(t *testing.T) {
		_, _, err := runToolboxCLI(t, []string{"sandbox", "process", "logs", "sbx", "p1", "--follow"}, []toolboxExpectedRequest{
			{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
			{method: http.MethodGet, path: "/test-tools/sbx/processes/p1/logs", status: 200, auth: true, query: url.Values{"encoding": {"text"}, "follow": {"true"}, "limit": {"256"}}, responseContentType: "text/event-stream", response: "event: state\ndata: {\"cursor\":\"c1\",\"state\":\"terminal\",\"reason\":\"exited\",\"exitCode\":17}\n\nevent: eof\ndata: {\"cursor\":\"c1\"}\n\n"},
		})
		require.Equal(t, 17, cerr.ExitCode(err))
	})

	t.Run("follow outputs NDJSON in json mode", func(t *testing.T) {
		stdout, stderr, err := runToolboxCLI(t, []string{"sandbox", "process", "logs", "sbx", "p1", "--follow"}, []toolboxExpectedRequest{
			{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
			{method: http.MethodGet, path: "/test-tools/sbx/processes/p1/logs", status: 200, auth: true, query: url.Values{"encoding": {"text"}, "follow": {"true"}, "limit": {"256"}}, responseContentType: "text/event-stream", response: "event: log\nid: c1\ndata: {\"channel\":\"stdout\",\"data\":\"hello\\n\",\"cursor\":\"c1\",\"timestamp\":1}\n\nevent: state\ndata: {\"cursor\":\"c2\",\"state\":\"terminal\",\"reason\":\"exited\",\"exitCode\":17}\n\nevent: eof\ndata: {\"cursor\":\"c2\"}\n\n"},
		})
		require.Equal(t, 17, cerr.ExitCode(err))
		require.Equal(t, "{\"channel\":\"stdout\",\"cursor\":\"c1\",\"data\":\"hello\\n\",\"timestamp\":1}\n", stdout)
		require.Empty(t, stderr)
	})

	t.Run("follow threads requested limit", func(t *testing.T) {
		_, _, err := runToolboxCLI(t, []string{"sandbox", "process", "logs", "sbx", "p1", "--follow", "--limit", "9"}, []toolboxExpectedRequest{
			{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
			{method: http.MethodGet, path: "/test-tools/sbx/processes/p1/logs", status: 200, auth: true, query: url.Values{"encoding": {"text"}, "follow": {"true"}, "limit": {"9"}}, responseContentType: "text/event-stream", response: "event: eof\ndata: {\"cursor\":\"c1\"}\n\n"},
		})
		require.NoError(t, err)
	})

	t.Run("follow errors when stream ends before eof in table mode", func(t *testing.T) {
		_, _, err := runToolboxCLI(t, []string{"--output", "table", "sandbox", "process", "logs", "sbx", "p1", "--follow"}, []toolboxExpectedRequest{
			{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
			{method: http.MethodGet, path: "/test-tools/sbx/processes/p1/logs", status: 200, auth: true, query: url.Values{"encoding": {"text"}, "follow": {"true"}, "limit": {"256"}}, responseContentType: "text/event-stream", response: "event: state\ndata: {\"cursor\":\"c1\",\"state\":\"terminal\",\"reason\":\"exited\",\"exitCode\":0}\n\n"},
		})
		require.Equal(t, cerr.ExitFailure, cerr.ExitCode(err))
		require.EqualError(t, err, "stream ended before the process finished")
	})

	t.Run("follow errors when stream ends before eof in json mode", func(t *testing.T) {
		_, _, err := runToolboxCLI(t, []string{"sandbox", "process", "logs", "sbx", "p1", "--follow"}, []toolboxExpectedRequest{
			{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
			{method: http.MethodGet, path: "/test-tools/sbx/processes/p1/logs", status: 200, auth: true, query: url.Values{"encoding": {"text"}, "follow": {"true"}, "limit": {"256"}}, responseContentType: "text/event-stream", response: "event: log\ndata: {\"channel\":\"stdout\",\"cursor\":\"c1\",\"data\":\"hello\\n\",\"timestamp\":1}\n\nevent: state\ndata: {\"cursor\":\"c2\",\"state\":\"terminal\",\"reason\":\"exited\",\"exitCode\":0}\n\n"},
		})
		require.Equal(t, cerr.ExitFailure, cerr.ExitCode(err))
		require.EqualError(t, err, "stream ended before the process finished")
	})

	t.Run("follow rejects yaml output", func(t *testing.T) {
		_, _, err := runToolboxCLIWithOutput(t, outputArgYAML, []string{"sandbox", "process", "logs", "sbx", "p1", "--follow"}, nil)
		require.Error(t, err)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	})
}

const outputArgYAML = "yaml"

func TestSandboxProcessLogsFollow_timeoutReturnsExitTimeout(t *testing.T) {
	serverURL := ""
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/sbx":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, strings.ReplaceAll(sandboxDetailFixture("sbx", "linux", "running"), "https://toolbox.example", serverURL+"/test-tools"))
		case "/test-tools/sbx/processes/p1/logs":
			w.Header().Set("Content-Type", "text/event-stream")
			w.WriteHeader(http.StatusOK)
			if flusher, ok := w.(http.Flusher); ok {
				flusher.Flush()
			}
			<-r.Context().Done()
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.String())
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	serverURL = server.URL

	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", server.URL, "--api-key", "k", "sandbox", "process", "logs", "sbx", "p1", "--follow", "--timeout", "10ms")
	require.Equal(t, cerr.ExitTimeout, code, stderr)
	require.Empty(t, stdout)
	require.Contains(t, stderr, "timed out after 10ms waiting for the process to finish")
}

func TestSandboxProcessLogsFollow_cancelAfterStreamEstablishedReturnsInterrupted(t *testing.T) {
	started := make(chan struct{}, 1)
	serverURL := ""
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/sbx":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, strings.ReplaceAll(sandboxDetailFixture("sbx", "linux", "running"), "https://toolbox.example", serverURL+"/test-tools"))
		case "/test-tools/sbx/processes/p1/logs":
			w.Header().Set("Content-Type", "text/event-stream")
			w.WriteHeader(http.StatusOK)
			_, _ = io.WriteString(w, "event: log\ndata: {\"channel\":\"stdout\",\"cursor\":\"c1\",\"data\":\"hello\",\"timestamp\":1}\n\n")
			if flusher, ok := w.(http.Flusher); ok {
				flusher.Flush()
			}
			started <- struct{}{}
			<-r.Context().Done()
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.String())
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	serverURL = server.URL

	toolboxEnvMu.Lock()
	defer toolboxEnvMu.Unlock()
	t.Setenv("DAYTONA_CONFIG_DIR", t.TempDir())
	t.Setenv("DAYTONA_API_URL", "")
	t.Setenv("DAYTONA_API_KEY", "")
	t.Setenv("DAYTONA_PROFILE", "")
	t.Setenv("DAYTONA_ORG", "")
	t.Setenv("DAYTONA_PROJECT", "")
	t.Setenv("DAYTONA_REGION", "")
	t.Setenv("DAYTONA_OIDC_CLIENT_ID", "")
	t.Setenv("DAYTONA_YES", "")
	t.Setenv("CI", "1")

	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	root := NewRoot(stdout, stderr)
	root.SetArgs([]string{"--api-url", server.URL, "--api-key", "k", "sandbox", "process", "logs", "sbx", "p1", "--follow"})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	errCh := make(chan error, 1)
	go func() {
		errCh <- root.ExecuteContext(ctx)
	}()
	select {
	case <-started:
	case err := <-errCh:
		t.Fatalf("the command ended before the stream started: %v; stderr: %s", err, stderr.String())
	}
	cancel()
	err := <-errCh
	require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
	require.EqualError(t, err, "interrupted")
}
