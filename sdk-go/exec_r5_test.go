// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestExecR5PreservesPartialResult_whenPostCreateRequestFails(t *testing.T) {
	for _, sessionRun := range []bool{false, true} {
		for _, scenario := range []string{"wait", "logs", "continuation", "stream wait"} {
			name := "exec"
			if sessionRun {
				name = "session"
			}
			t.Run(name+"/"+scenario, func(t *testing.T) {
				// Given a created command with retained output and a failed later request.
				info := processFixture("p")
				inline := processLogsFixture(map[string]any{
					"cursor": "1", "timestamp": 1, "channel": "stdout", "data": "first",
				})
				inline["eof"], inline["nextCursor"] = false, "1"
				if scenario == "wait" {
					info["logs"] = inline
				}
				sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{
					"POST /processes": jsonHandler(t, info),
					"GET /processes/p/wait": func(w http.ResponseWriter, _ *http.Request) {
						if scenario == "wait" || scenario == "stream wait" {
							w.WriteHeader(http.StatusServiceUnavailable)
							return
						}
						writeJSON(t, w, map[string]any{"reason": "exited", "exitCode": 0})
					},
					"GET /processes/p/logs": func(w http.ResponseWriter, r *http.Request) {
						if scenario == "stream wait" {
							interpreterSSE(t, w, "event: log\nid: 1\ndata: "+
								"{\"channel\":\"stdout\",\"data\":\"first\",\"timestamp\":1}\n\n"+
								"event: eof\nid: 1\ndata: {}\n\n")
							return
						}
						if scenario == "continuation" && r.URL.Query().Get("cursor") == "" {
							writeJSON(t, w, inline)
							return
						}
						w.WriteHeader(http.StatusServiceUnavailable)
					},
				})
				var stdout bytes.Buffer
				options := &ExecOptions{}
				if scenario == "stream wait" {
					options.Stdout = &stdout
				}
				run := sandbox.Exec
				if sessionRun {
					session, err := sandbox.Process().ConnectSession("shell")
					require.NoError(t, err)
					run = func(ctx context.Context, cmd string, opts *ExecOptions) (*ProcessResult, error) {
						return session.Run(ctx, cmd, &SessionRunOptions{Stdout: opts.Stdout})
					}
				}
				// When observation fails after creation.
				result, err := run(t.Context(), "command", options)
				// Then the reconnectable identity and all observed output survive.
				require.ErrorIs(t, err, ErrServiceUnavailable)
				require.NotNil(t, result)
				require.Equal(t, "p", result.ProcessID)
				require.Equal(t, ProcessReasonUnknown, result.Reason)
				require.Nil(t, result.ExitCode)
				want := "first"
				if scenario == "logs" {
					want = ""
				}
				require.Equal(t, want, result.Stdout)
				require.Equal(t, want, result.Output)
			})
		}
	}
}

func TestExecR5PreservesPartialResult_whenTimeoutSnapshotFails(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given a stream with delivered output and a snapshot that fails on page two.
		mux := http.NewServeMux()
		mux.HandleFunc("POST /processes", jsonHandler(t, processFixture("p")))
		mux.HandleFunc("GET /processes/p/logs", func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Query().Get("cursor") != "" {
				w.WriteHeader(http.StatusServiceUnavailable)
				return
			}
			page := processLogsFixture(map[string]any{
				"cursor": "2", "timestamp": 2, "channel": "stderr", "data": "snapshot",
			})
			page["eof"], page["nextCursor"] = false, "2"
			writeJSON(t, w, page)
		})
		sandbox := dockerTimingSandbox(t, func(r *http.Request) (*http.Response, error) {
			if r.URL.Query().Get("follow") != "true" {
				return (waitTestTransport{handler: mux.ServeHTTP}).RoundTrip(r)
			}
			body := io.MultiReader(strings.NewReader("event: log\nid: 1\ndata: "+
				"{\"timestamp\":1,\"channel\":\"stdout\",\"data\":\"first\"}\n\n"),
				execR3CanceledReader{ctx: r.Context()})
			return &http.Response{
				StatusCode: http.StatusOK, Header: http.Header{"Content-Type": {"text/event-stream"}},
				Body: io.NopCloser(body), Request: r,
			}, nil
		})
		var stdout bytes.Buffer
		// When the wait budget expires and replay fails.
		result, err := sandbox.Exec(t.Context(), "command", &ExecOptions{Timeout: time.Second, Stdout: &stdout})
		// Then live output and earlier snapshot pages are retained without claiming an outcome.
		require.ErrorIs(t, err, ErrServiceUnavailable)
		require.NotNil(t, result)
		require.Equal(t, "p", result.ProcessID)
		require.Equal(t, ProcessReasonUnknown, result.Reason)
		require.Equal(t, "first", result.Stdout)
		require.Equal(t, "snapshot", result.Stderr)
		require.Equal(t, "firstsnapshot", result.Output)
	})
}

func TestExecR5DiagnosticNamesGoField_whenOptionsInvalid(t *testing.T) {
	for _, tc := range []struct {
		field   string
		options ExecOptions
	}{
		{"Timeout", ExecOptions{Timeout: -time.Nanosecond}},
		{"KeepLogs", ExecOptions{KeepLogs: ProcessKeepLogsNone}},
	} {
		t.Run(tc.field, func(t *testing.T) {
			// Given an invalid Go option.
			sandbox, fake := processTestSandbox(t, nil)
			// When the command is refused locally.
			_, err := sandbox.Exec(t.Context(), "command", &tc.options)
			// Then the diagnostic names the caller's field without sending a request.
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, tc.field)
			require.Empty(t, fake.requests())
		})
	}
}
