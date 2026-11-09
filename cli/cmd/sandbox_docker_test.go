// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestSandboxDockerStatus_rendersStatusInTableMode(t *testing.T) {
	stdout, _, err := runToolboxCLIWithOutput(t, "table", []string{"sandbox", "docker", "status", "sbx"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodGet, path: "/test-tools/sbx/docker/status", status: 200, auth: true, response: `{"state":"ready","owner":"daemon","reason":"socket ready","processId":"proc-1"}`},
	})
	require.NoError(t, err)
	require.Contains(t, stdout, "state")
	require.Contains(t, stdout, "ready")
	require.Contains(t, stdout, "owner")
	require.Contains(t, stdout, "daemon")
	require.Contains(t, stdout, "reason")
	require.Contains(t, stdout, "socket ready")
	require.Contains(t, stdout, "process_id")
	require.Contains(t, stdout, "proc-1")
}

func TestSandboxDockerStatus_waitPollsUntilReady(t *testing.T) {
	stdout, _, err := runToolboxCLI(t, []string{"sandbox", "docker", "status", "sbx", "--wait"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodGet, path: "/test-tools/sbx/docker/status", status: 200, auth: true, query: url.Values{"waitMs": {"1000"}}, response: `{"state":"starting","owner":"daemon","reason":"booting","processId":"proc-1"}`},
		{method: http.MethodGet, path: "/test-tools/sbx/docker/status", status: 200, auth: true, query: url.Values{"waitMs": {"1000"}}, response: `{"state":"ready","owner":"daemon","processId":"proc-1"}`},
	})
	require.NoError(t, err)
	out := requireJSONStdout(t, stdout)
	require.Equal(t, "ready", out["state"])
	require.Equal(t, "daemon", out["owner"])
	require.Equal(t, "proc-1", out["process_id"], "toolbox output is snake_case")
	require.NotContains(t, out, "processId")
}

func TestSandboxDockerStatus_waitFailsOnTerminalReason(t *testing.T) {
	_, _, err := runToolboxCLI(t, []string{"sandbox", "docker", "status", "sbx", "--wait"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodGet, path: "/test-tools/sbx/docker/status", status: 200, auth: true, query: url.Values{"waitMs": {"1000"}}, response: `{"state":"failed","owner":"daemon","reason":"dockerd exited","processId":"proc-1"}`},
	})
	require.Error(t, err)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
	require.ErrorContains(t, err, "docker is failed: dockerd exited")
	var ce *cerr.Error
	require.True(t, errors.As(err, &ce))
	require.Contains(t, ce.Hint, "daytona sandbox process logs sbx proc-1")
}

func TestSandboxDockerStatus_waitHonoursTimeoutWhenStatusRequestStalls(t *testing.T) {
	serverURL := ""
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/sbx":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, strings.ReplaceAll(toolboxSandboxDetailFixture("sbx", "linux", "running"), "__SERVER__", serverURL))
		case "/test-tools/sbx/docker/status":
			// Handler goroutine: assert and keep serving, never FailNow.
			assert.NotEmpty(t, r.URL.Query().Get("waitMs"))
			<-r.Context().Done()
		default:
			t.Errorf("unexpected path %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()
	serverURL = srv.URL

	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	root := NewRoot(stdout, stderr)
	root.SetArgs([]string{"--api-url", srv.URL, "--api-key", "k", "-o", "json", "sandbox", "docker", "status", "sbx", "--wait", "--timeout", "1s"})
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	err := root.ExecuteContext(ctx)
	require.Error(t, err)
	require.Equal(t, cerr.ExitTimeout, cerr.ExitCode(err))
	require.ErrorContains(t, err, "waiting for Docker to become ready")
}
