// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"net/http"
	"syscall"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

type execDeliveryWriter struct {
	cancel context.CancelFunc
	cause  error
}

func (w execDeliveryWriter) Write([]byte) (int, error) {
	if w.cancel != nil {
		w.cancel()
	}
	return 0, w.cause
}

func TestExec_whenWriterFails_stopsProcessAndPreservesIdentity(t *testing.T) {
	for _, channel := range []string{"stdout", "stderr"} {
		for _, scenario := range []string{"success", "cleanup refused", "caller canceled", "short write"} {
			t.Run(channel+"/"+scenario, func(t *testing.T) {
				// Given a created process with live output and a failing destination.
				ctx, cancel := context.WithCancel(t.Context())
				defer cancel()
				cause := error(syscall.EPIPE)
				writer := execDeliveryWriter{cause: cause}
				if scenario == "caller canceled" {
					writer.cancel = cancel
				}
				if scenario == "short write" {
					writer.cause, cause = nil, io.ErrShortWrite
				}
				signals := 0
				sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
					"POST /processes": jsonHandler(t, processFixture("p")),
					"GET /processes/p/logs": processSSEHandler(t,
						"event: log\nid: 1\ndata: {\"channel\":\""+channel+"\",\"data\":\"out\",\"timestamp\":1}\n\n"+
							"event: eof\nid: 1\ndata: {}\n\n"),
					"POST /processes/p/signals": func(w http.ResponseWriter, r *http.Request) {
						signals++
						assert.NoError(t, r.Context().Err())
						if scenario == "cleanup refused" {
							w.WriteHeader(http.StatusServiceUnavailable)
							return
						}
						w.WriteHeader(http.StatusNoContent)
					},
				})
				options := &ExecOptions{Stdout: writer}
				if channel == "stderr" {
					options.Stdout, options.Stderr = nil, writer
				}
				// When the local writer fails, even concurrently with cancellation.
				result, err := sandbox.Exec(ctx, "command", options)
				// Then cleanup is attempted without replacing the local error or identity.
				require.ErrorIs(t, err, cause)
				require.NotErrorIs(t, err, ErrConnection)
				var sdkError *Error
				require.ErrorAs(t, err, &sdkError)
				require.NotNil(t, result)
				require.Equal(t, "p", result.ProcessID)
				require.Equal(t, ProcessReasonUnknown, result.Reason)
				require.Equal(t, "out", result.Output)
				require.Nil(t, result.ExitCode)
				require.Equal(t, 1, signals)
				requests := fake.requests()
				require.JSONEq(t, `{"signal":"SIGTERM"}`, string(requests[len(requests)-1].Body))
			})
		}
	}
}

func TestExec_whenWriterCleanupStalls_boundsDetachedBudget(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given cancellation during a local write and a signal endpoint that stalls.
		ctx, cancel := context.WithCancel(t.Context())
		defer cancel()
		mux := http.NewServeMux()
		mux.HandleFunc("POST /processes", jsonHandler(t, processFixture("p")))
		mux.HandleFunc("GET /processes/p/logs", processSSEHandler(t,
			"event: log\nid: 1\ndata: {\"channel\":\"stdout\",\"data\":\"out\",\"timestamp\":1}\n\n"))
		var budget time.Duration
		mux.HandleFunc("POST /processes/p/signals", func(_ http.ResponseWriter, r *http.Request) {
			assert.NoError(t, r.Context().Err())
			deadline, ok := r.Context().Deadline()
			if !assert.True(t, ok) {
				return
			}
			budget = time.Until(deadline)
			<-r.Context().Done()
		})
		sandbox := dockerTimingSandbox(t, waitTestTransport{handler: mux.ServeHTTP}.RoundTrip)
		started := time.Now()
		// When cleanup cannot complete.
		result, err := sandbox.Exec(ctx, "command", &ExecOptions{
			Stdout: execDeliveryWriter{cancel: cancel, cause: syscall.EPIPE},
		})
		// Then the detached attempt has a five-second bound and preserves the failure.
		require.ErrorIs(t, err, syscall.EPIPE)
		require.NotNil(t, result)
		require.Equal(t, "p", result.ProcessID)
		require.Equal(t, 5*time.Second, budget)
		require.Equal(t, budget, time.Since(started))
	})
}
