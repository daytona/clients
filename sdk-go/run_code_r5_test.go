// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"net/http"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestRunCodeR5PreservesPartialResult_whenNonstreamObservationFails(t *testing.T) {
	for _, scenario := range []string{"wait", "logs", "continuation", "rich output"} {
		t.Run(scenario, func(t *testing.T) {
			// Given a created code process with a failing waiter or output read.
			routes := interpreterRunRoutes(t)
			routes["GET /processes/p/wait"] = func(w http.ResponseWriter, _ *http.Request) {
				if scenario == "wait" {
					w.WriteHeader(http.StatusServiceUnavailable)
					return
				}
				writeJSON(t, w, map[string]any{"reason": "exited", "exitCode": 0})
			}
			routes["GET /processes/p/logs"] = func(w http.ResponseWriter, r *http.Request) {
				if scenario == "logs" || r.URL.Query().Get("cursor") != "" {
					w.WriteHeader(http.StatusServiceUnavailable)
					return
				}
				frames := []any{map[string]any{
					"cursor": "1", "timestamp": 1, "channel": "stdout", "data": "first",
				}}
				if scenario == "rich output" {
					frames = append(frames, map[string]any{
						"cursor": "2", "timestamp": 2, "channel": "display",
						"data": `{"value":` + strings.Repeat("[", 601) + "0" + strings.Repeat("]", 601) + "}",
					})
				}
				writeJSON(t, w, map[string]any{
					"frames": frames, "eof": scenario == "rich output", "nextCursor": "1", "truncatedHead": false,
				})
			}
			sandbox := interpreterTestSandbox(t, newFakeServer(t, routes))
			// When a blocking run fails after the process exists.
			result, err := sandbox.RunCode(t.Context(), "1", nil)
			// Then partial output survives alongside the original error and identity.
			wantErr := ErrServiceUnavailable
			if scenario == "rich output" {
				wantErr = ErrBadRequest
			}
			require.ErrorIs(t, err, wantErr)
			require.NotNil(t, result)
			require.Equal(t, "p", result.Process.ProcessID)
			require.Equal(t, ProcessReasonUnknown, result.Process.Reason)
			require.Nil(t, result.Process.ExitCode)
			want := ""
			if scenario == "continuation" || scenario == "rich output" {
				want = "first"
			}
			require.Equal(t, want, result.Logs.Stdout)
		})
	}
}

func TestRunCodeR5PreservesPartialResult_whenTimeoutSnapshotFails(t *testing.T) {
	for _, scenario := range []string{"read", "continuation", "writer", "callback"} {
		t.Run(scenario, func(t *testing.T) {
			// Given a timed-out run with streamed output and a failing snapshot.
			delivered := make(chan struct{})
			routes := interpreterRunRoutes(t)
			routes["GET /processes/p/wait"] = func(w http.ResponseWriter, r *http.Request) {
				select {
				case <-delivered:
					writeJSON(t, w, map[string]any{"reason": "timed_out", "exitCode": 124})
				case <-r.Context().Done():
				}
			}
			terminal := interpreterProcessFixture("p")
			terminal["state"], terminal["reason"], terminal["exitCode"] = "terminal", "timed_out", 124
			routes["GET /processes/p"] = jsonHandler(t, terminal)
			routes["GET /processes/p/logs"] = func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Query().Get("follow") == "true" {
					interpreterSSE(t, w, "event: log\nid: 1\ndata: "+
						"{\"timestamp\":1,\"channel\":\"stdout\",\"data\":\"first\"}\n\n")
					assert.NoError(t, http.NewResponseController(w).Flush())
					<-r.Context().Done()
					return
				}
				if scenario == "read" || r.URL.Query().Get("cursor") != "" {
					w.WriteHeader(http.StatusServiceUnavailable)
					return
				}
				writeJSON(t, w, map[string]any{
					"frames": []any{map[string]any{
						"cursor": "2", "timestamp": 2, "channel": "stderr", "data": "snapshot",
					}}, "eof": scenario != "continuation", "nextCursor": "2", "truncatedHead": false,
				})
			}
			routes["POST /processes/p/signals"] = func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(http.StatusNoContent)
			}
			fake := newFakeServer(t, routes)
			sandbox := interpreterTestSandbox(t, fake)
			var stdout bytes.Buffer
			options := RunCodeOptions{
				Stdout: &stdout,
				OnLog: func(frame LogFrame) error {
					if frame.Cursor == "1" {
						close(delivered)
					} else if scenario == "callback" {
						return syscall.EPIPE
					}
					return nil
				},
			}
			if scenario == "writer" {
				options.Stderr = execDeliveryWriter{cause: syscall.EPIPE}
			}
			// When the post-timeout snapshot cannot be read or delivered.
			result, err := sandbox.RunCode(t.Context(), "1", &options)
			// Then both delivery and read failures preserve the live result.
			wantErr := error(ErrServiceUnavailable)
			if scenario == "writer" || scenario == "callback" {
				wantErr = syscall.EPIPE
			}
			require.ErrorIs(t, err, wantErr)
			require.NotNil(t, result)
			require.Equal(t, "p", result.Process.ProcessID)
			require.Equal(t, ProcessReasonUnknown, result.Process.Reason)
			require.Nil(t, result.Process.ExitCode)
			require.Equal(t, "first", result.Logs.Stdout)
			wantStderr := ""
			if scenario == "writer" || scenario == "continuation" {
				wantStderr = "snapshot"
			}
			require.Equal(t, wantStderr, result.Logs.Stderr)
			if scenario == "writer" || scenario == "callback" {
				require.Equal(t, "/processes/p/signals", fake.requests()[len(fake.requests())-1].Path)
			} else {
				for _, request := range fake.requests() {
					require.NotEqual(t, "/processes/p/signals", request.Path)
				}
			}
		})
	}
}

func TestRunCodeR5DiagnosticNamesGoField_whenOptionsInvalid(t *testing.T) {
	for _, tc := range []struct {
		field   string
		options RunCodeOptions
	}{
		{"Timeout", RunCodeOptions{Timeout: -time.Nanosecond}},
		{"Isolated", RunCodeOptions{Isolated: true, ContextID: "ctx", Language: "python"}},
		{"Env", RunCodeOptions{Env: map[string]string{}, ContextID: "ctx", Language: "python"}},
		{"Context", RunCodeOptions{Context: &InterpreterContext{}}},
		{"Language", RunCodeOptions{ContextID: "ctx", Language: "ruby"}},
	} {
		t.Run(tc.field, func(t *testing.T) {
			// Given invalid options, including a bound Python context for the language mismatch.
			fake := newFakeServer(t, nil)
			sandbox := interpreterTestSandbox(t, fake)
			options := tc.options
			if tc.field == "Language" {
				bound, err := sandbox.Interpreter().ConnectContext("ctx", "python")
				require.NoError(t, err)
				options.Context, options.ContextID = bound, ""
			}
			// When the caller invokes code execution.
			_, err := sandbox.RunCode(t.Context(), "1", &options)
			// Then local diagnostics identify the Go field.
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, tc.field)
			require.Empty(t, fake.requests())
		})
	}
}
