// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestSandboxTransition_whenRetryHintVaries(t *testing.T) {
	for _, tc := range []struct {
		name string
		hint any
		want time.Duration
	}{
		{"absent", nil, time.Second},
		{"zero", 0, time.Second},
		{"negative", -4, time.Second},
		{"short", 2, 2 * time.Second},
		{"maximum", 5, 5 * time.Second},
		{"large", 9223372036854775807, 5 * time.Second},
		{"mistyped", "4", time.Second},
		{"fractional", 2.5, time.Second},
	} {
		t.Run(tc.name, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given one transition refusal with competing HTTP/millisecond hints.
				calls := 0
				sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
					calls++
					if calls == 1 {
						w.Header().Set("Content-Type", "application/json")
						w.Header().Set("Retry-After", "4")
						w.WriteHeader(http.StatusConflict)
						writeJSON(t, w, map[string]any{
							"code": CodeSandboxTransitionInProgress, "source": SourceAPI,
							"retry_after_seconds": tc.hint, "retryAfterMs": 3000,
						})
						return
					}
					w.WriteHeader(http.StatusNoContent)
				})
				started := time.Now()
				// When retrying the submission.
				_, err := sandbox.client.retrySandboxTransition(t.Context(), apiRequest{Method: http.MethodPost, Path: "/operation"})
				// Then only the seconds hint controls the bounded delay.
				require.NoError(t, err)
				require.Equal(t, tc.want, time.Since(started))
				require.Equal(t, 2, calls)
			})
		})
	}
}

func TestSandboxTransition_whenCodeIsNotTransition(t *testing.T) {
	for _, code := range []string{CodeConflict, CodeDiskNotReady, CodeNetworkPolicyTransitionInProgress, "FUTURE_CONFLICT"} {
		t.Run(code, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given a different conflict code.
				calls := 0
				sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
					calls++
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(http.StatusConflict)
					writeJSON(t, w, map[string]string{"code": code, "source": SourceAPI})
				})
				started := time.Now()
				// When submitting through the transition helper.
				_, err := sandbox.client.retrySandboxTransition(t.Context(), apiRequest{Path: "/operation"})
				// Then no generic conflict retry is introduced.
				require.ErrorIs(t, err, ErrConflict)
				require.Zero(t, time.Since(started))
				require.Equal(t, 1, calls)
			})
		})
	}
}

func TestSandboxTransition_whenRetryBudgetExpires(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given every attempt takes two seconds and returns a transition refusal.
		calls := 0
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
			calls++
			<-time.After(2 * time.Second)
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusConflict)
			writeJSON(t, w, map[string]any{
				"code": CodeSandboxTransitionInProgress, "source": SourceAPI,
				"retry_after_seconds": 4, "message": "still transitioning", "trace_id": "last-trace",
				"requestId": "last-request",
			})
		})
		started := time.Now()
		// When transition refusals consume the retry window.
		_, err := sandbox.client.retrySandboxTransition(t.Context(), apiRequest{Path: "/operation"})
		// Then elapsed request time counts, the final refusal is not replaced,
		// and no pause precedes a retry the window could not admit (26 s + 4 s).
		require.Equal(t, 26*time.Second, time.Since(started))
		require.Equal(t, 5, calls)
		require.ErrorIs(t, err, ErrSandboxTransitionInProgress)
		require.ErrorIs(t, err, ErrConflict)
		var failure *Error
		require.ErrorAs(t, err, &failure)
		require.Equal(t, "still transitioning", failure.Message)
		require.Equal(t, "last-trace", failure.TraceID)
		require.Equal(t, "last-request", failure.RequestID)
		require.Equal(t, SourceAPI, failure.Source)
		require.Equal(t, 409, failure.StatusCode)
	})
}

func TestSandboxTransition_whenCallerOrClientEndsDuringDelay(t *testing.T) {
	for _, end := range []string{"cancel", "deadline", "close"} {
		t.Run(end, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given a refusal followed by an interrupted retry delay.
				calls := 0
				refused := make(chan struct{})
				sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
					calls++
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(http.StatusConflict)
					writeJSON(t, w, map[string]any{"code": CodeSandboxTransitionInProgress, "retry_after_seconds": 5})
					close(refused)
				})
				ctx, cancel := context.WithTimeout(t.Context(), 500*time.Millisecond)
				defer cancel()
				done := make(chan error, 1)
				go func() {
					<-refused
					synctest.Wait()
					switch end {
					case "cancel":
						cancel()
					case "close":
						assert.NoError(t, sandbox.client.Close())
					case "deadline":
					}
				}()
				// When an operation is interrupted before its next attempt.
				go func() {
					_, err := sandbox.client.retrySandboxTransition(ctx, apiRequest{Path: "/operation"})
					done <- err
				}()
				err := <-done
				// Then cancellation wins without an extra request.
				want := context.Canceled
				switch end {
				case "deadline":
					want = context.DeadlineExceeded
				case "close":
					want = ErrClientClosed
				case "cancel":
				}
				require.ErrorIs(t, err, want)
				require.Equal(t, 1, calls)
			})
		})
	}
}
