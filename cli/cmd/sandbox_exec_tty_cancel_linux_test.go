// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestExecTTY_returnsProcessFailureOrInterrupt_whenCommandCannotContinue(t *testing.T) {
	for _, startFailure := range []bool{true, false} {
		name := "interrupted"
		if startFailure {
			name = "cannot start"
		}
		t.Run(name, func(t *testing.T) {
			// Given
			execTerminal(t)
			ctx, cancel := context.WithTimeout(t.Context(), 3*time.Second)
			defer cancel()
			var signals atomic.Int32
			serverURL := ""
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				switch r.URL.Path {
				case "/sandboxes/sbx":
					_, _ = io.WriteString(w, strings.ReplaceAll(toolboxSandboxDetailFixture("sbx", "linux", "running"), "__SERVER__", serverURL))
				case "/test-tools/sbx/processes":
					if startFailure {
						w.WriteHeader(http.StatusBadRequest)
						_, _ = io.WriteString(w, `{"code":"PROCESS_START_FAILED","source":"DAYTONA_DAEMON","message":"cannot spawn"}`)
					} else {
						_, _ = io.WriteString(w, `{"id":"proc","state":"running","kind":"pty","stdin":"open","cwd":"/","keepLogs":"until_cleanup","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z"}`)
					}
				case "/test-tools/sbx/processes/proc/logs":
					_, _ = io.WriteString(w, `{"frames":[],"nextCursor":"c0","truncatedHead":false,"eof":false}`)
				case "/test-tools/sbx/processes/proc/attach":
					conn, _, err := upgradeExecSocket(w, r)
					if assert.NoError(t, err) {
						defer conn.Close()
						cancel()
						_, _ = io.Copy(io.Discard, conn)
					}
				case "/test-tools/sbx/processes/proc/signals":
					signals.Add(1)
					w.WriteHeader(http.StatusNoContent)
				default:
					t.Errorf("unexpected request %s", r.URL.Path)
					w.WriteHeader(http.StatusNotFound)
				}
			}))
			defer srv.Close()
			serverURL = srv.URL
			t.Setenv("DAYTONA_CONFIG_DIR", cfgDir(t))
			root := NewRoot(&bytes.Buffer{}, &bytes.Buffer{})
			root.SetArgs([]string{"--api-url", srv.URL, "--api-key", "k", "sandbox", "exec", "sbx", "--tty", "--", "shell"})
			// When
			err := root.ExecuteContext(ctx)
			// Then
			if startFailure {
				require.Equal(t, 127, cerr.ExitCode(err))
				require.Equal(t, cerr.SourceProcess, cerr.DetailsOf(err).Source)
				require.Zero(t, signals.Load())
			} else {
				require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
				require.Equal(t, int32(1), signals.Load())
			}
		})
	}
}

func TestExec_preservesBytes_whenStdinIsTerminal(t *testing.T) {
	// Given
	execTerminal(t)
	// When
	stdout, _, err := runToolboxCLI(t, []string{"sandbox", "exec", "sbx", "--", "binary"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodPost, path: "/test-tools/sbx/processes", status: 201, auth: true, check: func(t *testing.T, _ *http.Request, body []byte) {
			// A terminal stdin is never forwarded: the process starts with stdin closed.
			var sent map[string]any
			if assert.NoError(t, json.Unmarshal(body, &sent)) {
				assert.Equal(t, "closed", sent["stdin"])
			}
		}, response: `{"id":"proc","state":"terminal","kind":"exec","stdin":"closed","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","reason":"exited","exitCode":0,"logs":{"frames":[{"channel":"stdout","cursor":"c1","data":"_wA=","timestamp":1}],"nextCursor":"c1","truncatedHead":false,"eof":true}}`},
	})
	// Then
	require.NoError(t, err)
	require.Equal(t, []byte{0xff, 0}, []byte(stdout))
}
