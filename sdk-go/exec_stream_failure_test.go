// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestExecPreservesPartialResult_whenLogStreamFails(t *testing.T) {
	for _, sessionRun := range []bool{false, true} {
		for _, failure := range []struct {
			name, tail string
			want       error
		}{
			{"dropped", "", ErrConnection},
			{"server error", "event: error\ndata: {\"code\":\"PAYLOAD_TOO_LARGE\",\"message\":\"large\",\"source\":\"SDK\"}\n\n", ErrPayloadTooLarge},
		} {
			name := "exec"
			if sessionRun {
				name = "session"
			}
			t.Run(name+"/"+failure.name, func(t *testing.T) {
				// Given a created process whose stream fails after delivering output.
				sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
					"POST /processes": jsonHandler(t, processFixture("p")),
					"GET /processes/p/logs": processSSEHandler(t,
						"event: log\nid: 1\ndata: {\"channel\":\"stdout\",\"data\":\"first\",\"timestamp\":1}\n\n"+failure.tail),
				})
				var stdout bytes.Buffer
				run := sandbox.Exec
				if sessionRun {
					session, err := sandbox.Process().ConnectSession("shell")
					require.NoError(t, err)
					run = func(ctx context.Context, command string, opts *ExecOptions) (*ProcessResult, error) {
						return session.Run(ctx, command, &SessionRunOptions{Stdout: opts.Stdout})
					}
				}
				// When the stream cannot complete.
				result, err := run(t.Context(), "command", &ExecOptions{Stdout: &stdout})
				// Then callers keep the identity and delivered output, without a false outcome.
				require.ErrorIs(t, err, failure.want)
				require.NotNil(t, result)
				require.Equal(t, "p", result.ProcessID)
				require.Equal(t, ProcessReasonUnknown, result.Reason)
				require.Nil(t, result.ExitCode)
				require.Equal(t, "first", result.Stdout)
				require.Equal(t, result.Stdout, stdout.String())
				require.Len(t, result.Logs, 1)
				require.Len(t, fake.requests(), 2) // No retry or stop on stream failure.
			})
		}
	}
}

func TestRunCodePreservesPartialResult_whenLogStreamFails(t *testing.T) {
	for _, failure := range []struct {
		name, tail string
		want       error
	}{
		{"dropped", "", ErrConnection},
		{"server error", "event: error\ndata: {\"code\":\"PAYLOAD_TOO_LARGE\",\"message\":\"large\",\"source\":\"SDK\"}\n\n", ErrPayloadTooLarge},
	} {
		t.Run(failure.name, func(t *testing.T) {
			// Given a live code process; its waiter must be joined on stream failure.
			waitStarted, waitStopped := make(chan struct{}), make(chan struct{})
			routes := interpreterRunRoutes(t)
			routes["GET /processes/p/wait"] = func(_ http.ResponseWriter, r *http.Request) {
				close(waitStarted)
				<-r.Context().Done()
				close(waitStopped)
			}
			routes["GET /processes/p/logs"] = func(w http.ResponseWriter, _ *http.Request) {
				<-waitStarted
				interpreterSSE(t, w,
					"event: log\nid: 1\ndata: {\"channel\":\"stdout\",\"data\":\"first\",\"timestamp\":1}\n\n"+failure.tail)
			}
			fake := newFakeServer(t, routes)
			sandbox := interpreterTestSandbox(t, fake)
			var stdout bytes.Buffer
			// When a streaming code run loses observation.
			result, err := sandbox.RunCode(t.Context(), "1", &RunCodeOptions{Stdout: &stdout})
			// Then partial output and a reconnectable identity survive alongside the error.
			require.ErrorIs(t, err, failure.want)
			require.NotNil(t, result)
			require.Equal(t, "p", result.Process.ProcessID)
			require.Equal(t, ProcessReasonUnknown, result.Process.Reason)
			require.Nil(t, result.Process.ExitCode)
			require.Equal(t, "first", result.Logs.Stdout)
			require.Equal(t, result.Logs.Stdout, stdout.String())
			<-waitStopped
			require.Len(t, fake.requests(), 3) // No retry or interrupt on stream failure.
		})
	}
}

func TestStreamLogsExplainsIncompleteLog_whenStreamEndsBeforeEOF(t *testing.T) {
	// Given a stream that delivers one frame without the required eof.
	sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{
		"GET /processes/p/logs": processSSEHandler(t,
			"event: log\nid: 1\ndata: {\"channel\":\"stdout\",\"data\":\"first\",\"timestamp\":1}\n\n"),
	})
	handle, err := sandbox.Process().Connect("p")
	require.NoError(t, err)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	// When reading through its unexpected end.
	var failure error
	for _, streamErr := range handle.StreamLogs(ctx, nil) {
		failure = streamErr
	}
	// Then the diagnostic names the operation without losing error classification.
	require.ErrorIs(t, failure, ErrConnection)
	require.ErrorIs(t, failure, io.ErrUnexpectedEOF)
	require.ErrorContains(t, failure, "process log stream ended before eof")
}
