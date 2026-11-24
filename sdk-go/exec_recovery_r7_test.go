// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func execRecoverySandbox(t *testing.T, replay http.HandlerFunc) *Sandbox {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc("POST /processes", jsonHandler(t, processFixture("p")))
	mux.HandleFunc("GET /processes/p/logs", replay)
	mux.HandleFunc("POST /processes/p/signals", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	})
	return dockerTimingSandbox(t, func(r *http.Request) (*http.Response, error) {
		if r.URL.Query().Get("follow") != "true" {
			return (waitTestTransport{handler: mux.ServeHTTP}).RoundTrip(r)
		}
		var stream strings.Builder
		for cursor := 1; cursor <= 12; cursor++ {
			fmt.Fprintf(&stream, "event: log\nid: %d\ndata: {\"channel\":\"stdout\",\"data\":\"live\",\"timestamp\":1}\n\n", cursor)
		}
		body := io.MultiReader(strings.NewReader(stream.String()), execR3CanceledReader{ctx: r.Context()})
		return &http.Response{
			StatusCode: http.StatusOK, Header: http.Header{"Content-Type": {"text/event-stream"}},
			Body: io.NopCloser(body), Request: r,
		}, nil
	})
}

func TestExecRecoveryResumesCursor_whenStreamTimesOut(t *testing.T) {
	for _, operation := range []string{"exec", "session"} {
		t.Run(operation, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given more than eight pages of live history and continuously growing logs.
				var cursors []string
				sandbox := execRecoverySandbox(t, func(w http.ResponseWriter, r *http.Request) {
					cursor := r.URL.Query().Get("cursor")
					cursors = append(cursors, cursor)
					index := 0
					if cursor != "" {
						var err error
						index, err = strconv.Atoi(cursor)
						assert.NoError(t, err)
					}
					frame := processFrameFixture("stdout", "tail")
					frame["cursor"] = strconv.Itoa(index + 1)
					if index < 12 {
						frame["data"] = "live"
					}
					page := processLogsFixture(frame)
					page["nextCursor"], page["eof"] = frame["cursor"], false
					writeJSON(t, w, page)
				})
				options := &ExecOptions{Timeout: time.Second, Stdout: io.Discard}
				run := sandbox.Exec
				if operation == "session" {
					session, err := sandbox.Process().ConnectSession("shell")
					require.NoError(t, err)
					run = func(ctx context.Context, cmd string, opts *ExecOptions) (*ProcessResult, error) {
						return session.Run(ctx, cmd, &SessionRunOptions{Timeout: opts.Timeout, Stdout: opts.Stdout})
					}
				}
				// When streaming observation times out.
				result, err := run(t.Context(), "command", options)
				// Then the bounded snapshot starts after delivered history, not at the log head.
				require.NoError(t, err)
				require.Equal(t, []string{"12", "13", "14", "15", "16", "17", "18", "19"}, cursors)
				require.Equal(t, ProcessReasonTimedOut, result.Reason)
				require.Len(t, result.Logs, 20)
				require.Equal(t, strings.Repeat("live", 12)+strings.Repeat("tail", 8), result.Stdout)
			})
		})
	}
}

func TestExecRecoveryDeliversWriters_whenSnapshotAddsFrames(t *testing.T) {
	for _, readFails := range []bool{false, true} {
		t.Run(strconv.FormatBool(readFails), func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given a timed-out stream and new frames on each output channel.
				pages := 0
				sandbox := execRecoverySandbox(t, func(w http.ResponseWriter, _ *http.Request) {
					pages++
					if pages == 2 {
						w.WriteHeader(http.StatusServiceUnavailable)
						return
					}
					page := processLogsFixture(
						map[string]any{"cursor": "13", "timestamp": 1, "channel": "pty", "data": "terminal"},
						map[string]any{"cursor": "14", "timestamp": 1, "channel": "stderr", "data": "error"},
						map[string]any{"cursor": "15", "timestamp": 1, "channel": "setup", "data": "setup"},
					)
					page["nextCursor"], page["eof"] = "15", !readFails
					writeJSON(t, w, page)
				})
				var output bytes.Buffer
				// When recovering retained output, even if a later page fails.
				result, err := sandbox.Exec(t.Context(), "command", &ExecOptions{
					Timeout: time.Second, Stdout: &output, Stderr: &output,
				})
				// Then writers receive the same ordered, channel-filtered output as the result.
				if readFails {
					require.ErrorIs(t, err, ErrServiceUnavailable)
					require.Equal(t, ProcessReasonUnknown, result.Reason)
				} else {
					require.NoError(t, err)
					require.Equal(t, ProcessReasonTimedOut, result.Reason)
				}
				require.Equal(t, strings.Repeat("live", 12)+"terminalerror", result.Output)
				require.Equal(t, result.Output, output.String())
			})
		})
	}
}

func TestExecRecoveryPreservesWriterFailure_whenSnapshotDeliveryFails(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given stderr arrives only in timeout recovery and its destination fails.
		frame := processFrameFixture("stderr", "recovered")
		frame["cursor"] = "13"
		sandbox := execRecoverySandbox(t, jsonHandler(t, processLogsFixture(frame)))
		var stdout bytes.Buffer
		// When writing a recovered frame fails.
		result, err := sandbox.Exec(t.Context(), "command", &ExecOptions{
			Timeout: time.Second, Stdout: &stdout, Stderr: execDeliveryWriter{cause: syscall.EPIPE},
		})
		// Then the local failure remains reachable and the result preserves identity and output.
		require.ErrorIs(t, err, syscall.EPIPE)
		require.Equal(t, "p", result.ProcessID)
		require.Equal(t, ProcessReasonUnknown, result.Reason)
		require.Equal(t, "recovered", result.Stderr)
	})
}
