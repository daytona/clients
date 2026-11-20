// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"net/http"
	"strconv"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestExecTimeout_whenCreateIsSlow_preservesProcessID(t *testing.T) {
	for _, entry := range []struct {
		name string
		run  func(context.Context, *Sandbox, *ExecOptions) (*ProcessResult, error)
	}{
		{"Exec", func(ctx context.Context, s *Sandbox, opts *ExecOptions) (*ProcessResult, error) {
			return s.Exec(ctx, "command", opts)
		}},
		{"ExecArgs", func(ctx context.Context, s *Sandbox, opts *ExecOptions) (*ProcessResult, error) {
			return s.ExecArgs(ctx, []string{"command"}, opts)
		}},
		{"Run", func(ctx context.Context, s *Sandbox, opts *ExecOptions) (*ProcessResult, error) {
			return s.Process().Run(ctx, "command", opts)
		}},
		{"RunArgs", func(ctx context.Context, s *Sandbox, opts *ExecOptions) (*ProcessResult, error) {
			return s.Process().RunArgs(ctx, []string{"command"}, opts)
		}},
		{"SessionRun", func(ctx context.Context, s *Sandbox, opts *ExecOptions) (*ProcessResult, error) {
			session, err := s.Process().ConnectSession("shell")
			if err != nil {
				return nil, err
			}
			return session.Run(ctx, "command", &SessionRunOptions{Timeout: opts.Timeout, Stdout: opts.Stdout})
		}},
	} {
		for _, streaming := range []bool{false, true} {
			t.Run(entry.name+"/streaming="+strconv.FormatBool(streaming), func(t *testing.T) {
				synctest.Test(t, func(t *testing.T) {
					// Given a 300 ms create and a process whose retained logs never end.
					pages := 0
					mux := http.NewServeMux()
					mux.HandleFunc("POST /processes", func(w http.ResponseWriter, r *http.Request) {
						timer := time.NewTimer(300 * time.Millisecond)
						defer timer.Stop()
						select {
						case <-timer.C:
							writeJSON(t, w, processFixture("p"))
						case <-r.Context().Done():
						}
					})
					mux.HandleFunc("GET /processes/p/logs", func(w http.ResponseWriter, r *http.Request) {
						if r.URL.Query().Get("follow") == "true" {
							<-r.Context().Done()
							return
						}
						pages++
						page := processLogsFixture(processFrameFixture("stdout", "x"))
						page["eof"], page["nextCursor"] = false, strconv.Itoa(pages)
						writeJSON(t, w, page)
					})
					sandbox := dockerTimingSandbox(t, waitTestTransport{handler: mux.ServeHTTP}.RoundTrip)
					options := &ExecOptions{Timeout: 100 * time.Millisecond}
					wantElapsed := 300 * time.Millisecond
					if streaming {
						options.Stdout = io.Discard
						wantElapsed += options.Timeout
					}
					started := time.Now()
					// When the create outlasts the observation budget.
					result, err := entry.run(t.Context(), sandbox, options)
					// Then the handle survives, with a bounded snapshot, not a request error.
					require.NoError(t, err)
					require.Equal(t, "p", result.ProcessID)
					require.Equal(t, ProcessReasonTimedOut, result.Reason)
					require.Nil(t, result.ExitCode)
					require.Equal(t, "xxxxxxxx", result.Stdout)
					require.Len(t, result.Logs, 8)
					require.Equal(t, 8, pages)
					require.Equal(t, wantElapsed, time.Since(started))
				})
			})
		}
	}
}

func TestExecStreaming_whenCreateIsSlow_usesRequestTimeout(t *testing.T) {
	for _, timeout := range []time.Duration{0, 100 * time.Millisecond, WaitForever, NoTimeout} {
		t.Run(timeout.String(), func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given a create slower than the configured request timeout.
				mux := http.NewServeMux()
				mux.HandleFunc("POST /processes", func(w http.ResponseWriter, r *http.Request) {
					timer := time.NewTimer(300 * time.Millisecond)
					defer timer.Stop()
					select {
					case <-timer.C:
						writeJSON(t, w, processFixture("p"))
					case <-r.Context().Done():
					}
				})
				mux.HandleFunc("GET /processes/p/logs", processSSEHandler(t, "event: eof\ndata: {\"cursor\":\"1\"}\n\n"))
				mux.HandleFunc("GET /processes/p/wait", jsonHandler(t, map[string]string{"reason": "exited"}))
				sandbox := dockerTimingSandbox(t, waitTestTransport{handler: mux.ServeHTTP}.RoundTrip)
				sandbox.client.config.timeout = 200 * time.Millisecond
				started := time.Now()
				// When streaming with a finite/default or explicitly unlimited wait.
				result, err := sandbox.Exec(t.Context(), "command", &ExecOptions{Timeout: timeout, Stdout: io.Discard})
				// Then only an explicit unlimited wait removes the create request timeout.
				if timeout == WaitForever || timeout == NoTimeout {
					require.NoError(t, err)
					require.Equal(t, "p", result.ProcessID)
					require.Equal(t, ProcessReasonExited, result.Reason)
					require.Equal(t, 300*time.Millisecond, time.Since(started))
				} else {
					require.ErrorIs(t, err, ErrClientTimeout)
					require.Nil(t, result)
					require.Equal(t, 200*time.Millisecond, time.Since(started))
				}
			})
		})
	}
}
