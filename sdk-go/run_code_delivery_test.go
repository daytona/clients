// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"syscall"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestRunCode_whenDeliveryFails_interruptsAndPreservesIdentity(t *testing.T) {
	for _, delivery := range []string{"stdout", "stderr", "output", "error", "log"} {
		for _, scenario := range []string{"success", "cleanup refused", "caller canceled"} {
			t.Run(delivery+"/"+scenario, func(t *testing.T) {
				// Given an active cell and a local destination that fails.
				ctx, cancel := context.WithCancel(t.Context())
				defer cancel()
				failure := error(syscall.EPIPE)
				fail := func() error {
					if scenario == "caller canceled" {
						cancel()
					}
					return failure
				}
				writer := execDeliveryWriter{cause: failure}
				if scenario == "caller canceled" {
					writer.cancel = cancel
				}
				channel := "stdout"
				options := RunCodeOptions{}
				switch delivery {
				case "stdout":
					options.Stdout = writer
				case "stderr":
					channel = "stderr"
					options.Stderr = writer
				case "output":
					channel = "display"
					options.OnOutput = func(InterpreterOutput) error { return fail() }
				case "error":
					channel = "error"
					options.OnError = func(InterpreterError) error { return fail() }
				case "log":
					options.OnLog = func(LogFrame) error { return fail() }
				}
				waitStarted := make(chan struct{})
				routes := interpreterRunRoutes(t)
				routes["GET /processes/p/wait"] = func(_ http.ResponseWriter, r *http.Request) {
					close(waitStarted)
					<-r.Context().Done()
				}
				routes["GET /processes/p/logs"] = func(w http.ResponseWriter, _ *http.Request) {
					<-waitStarted
					interpreterSSE(t, w, fmt.Sprintf(
						"event: log\nid: 1\ndata: {\"channel\":%q,\"data\":\"{}\",\"timestamp\":1}\n\n", channel))
				}
				routes["POST /processes/p/signals"] = func(w http.ResponseWriter, r *http.Request) {
					assert.NoError(t, r.Context().Err())
					if scenario == "cleanup refused" {
						w.WriteHeader(http.StatusServiceUnavailable)
						return
					}
					w.WriteHeader(http.StatusNoContent)
				}
				fake := newFakeServer(t, routes)
				sandbox := interpreterTestSandbox(t, fake)
				// When delivery fails after submission.
				result, err := sandbox.RunCode(ctx, "loop()", &options)
				// Then a detached SIGINT is attempted and neither error nor identity is lost.
				require.ErrorIs(t, err, failure)
				require.NotErrorIs(t, err, ErrConnection)
				var sdkError *Error
				require.ErrorAs(t, err, &sdkError)
				require.NotNil(t, result)
				require.Equal(t, "p", result.Process.ProcessID)
				require.Equal(t, ProcessReasonUnknown, result.Process.Reason)
				require.Nil(t, result.Process.ExitCode)
				requests := fake.requests()
				require.Len(t, requests, 4)
				require.Equal(t, http.MethodPost, requests[3].Method)
				require.Equal(t, "/processes/p/signals", requests[3].Path)
				require.JSONEq(t, `{"signal":"SIGINT"}`, string(requests[3].Body))
			})
		}
	}
}

func TestRunCode_whenDeliveryCleanupStalls_boundsDetachedBudget(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given a local callback error that also matches cancellation.
		ctx, cancel := context.WithCancel(t.Context())
		defer cancel()
		waitStarted := make(chan struct{})
		mux := http.NewServeMux()
		mux.HandleFunc("POST /processes", jsonHandler(t, interpreterProcessFixture("p")))
		mux.HandleFunc("GET /processes/p/wait", func(_ http.ResponseWriter, r *http.Request) {
			close(waitStarted)
			<-r.Context().Done()
		})
		mux.HandleFunc("GET /processes/p/logs", func(w http.ResponseWriter, _ *http.Request) {
			<-waitStarted
			interpreterSSE(t, w,
				"event: log\nid: 1\ndata: {\"channel\":\"stdout\",\"data\":\"out\",\"timestamp\":1}\n\n")
		})
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
		// When interruption stalls after the callback cancels its caller.
		result, err := sandbox.RunCode(ctx, "loop()", &RunCodeOptions{
			OnLog: func(LogFrame) error { cancel(); return context.Canceled },
		})
		// Then cleanup is detached but bounded, retaining the callback error and ID.
		require.ErrorIs(t, err, context.Canceled)
		require.NotNil(t, result)
		require.Equal(t, "p", result.Process.ProcessID)
		require.Equal(t, 5*time.Second, budget)
		require.Equal(t, budget, time.Since(started))
	})
}

func TestRunCode_whenFailureIsNotLocalDelivery_doesNotInterrupt(t *testing.T) {
	for _, scenario := range []string{"transport", "canceled", "malformed rich output"} {
		t.Run(scenario, func(t *testing.T) {
			// Given an active cell whose stream fails independently of its destination.
			ctx, cancel := context.WithCancel(t.Context())
			defer cancel()
			waitStarted := make(chan struct{})
			routes := interpreterRunRoutes(t)
			routes["GET /processes/p/wait"] = func(_ http.ResponseWriter, r *http.Request) {
				close(waitStarted)
				<-r.Context().Done()
			}
			routes["GET /processes/p/logs"] = func(w http.ResponseWriter, _ *http.Request) {
				<-waitStarted
				switch scenario {
				case "transport":
					interpreterSSE(t, w, ": incomplete\n\n")
				case "canceled":
					cancel()
				case "malformed rich output":
					payload := `{"value":` + strings.Repeat("[", 601) + "0" + strings.Repeat("]", 601) + "}"
					interpreterSSE(t, w, fmt.Sprintf(
						"event: log\nid: 1\ndata: {\"channel\":\"display\",\"data\":%q,\"timestamp\":1}\n\n", payload))
				}
			}
			fake := newFakeServer(t, routes)
			sandbox := interpreterTestSandbox(t, fake)
			// When no writer or callback has failed.
			_, err := sandbox.RunCode(ctx, "loop()", &RunCodeOptions{Stdout: io.Discard})
			// Then the original failure is returned with no signal request.
			var want error = ErrConnection
			switch scenario {
			case "canceled":
				want = context.Canceled
			case "malformed rich output":
				want = ErrBadRequest
			}
			require.ErrorIs(t, err, want)
			require.Len(t, fake.requests(), 3)
		})
	}
}
