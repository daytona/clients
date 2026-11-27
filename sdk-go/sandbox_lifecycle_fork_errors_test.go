// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestForkRequiresUsableResponseID(t *testing.T) {
	for name, body := range map[string]any{
		"null": nil, "array": []string{}, "missing": map[string]string{}, "empty": map[string]string{"id": ""},
		"blank": map[string]string{"id": " "}, "dot": map[string]string{"id": "."}, "parent": map[string]string{"id": ".."},
	} {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /sandboxes/source/fork": jsonHandler(t, body)})
			sandbox := newForkTestSource(t, fake)
			// When
			_, err := sandbox.Fork(t.Context(), &ForkOptions{Wait: NoWait})
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestForkReturnsAcceptedHandleAfterReadFailure(t *testing.T) {
	for _, status := range []int{403, 408, 500} {
		for _, failFirst := range []bool{false, true} {
			t.Run(http.StatusText(status)+map[bool]string{false: "/wait", true: "/detail"}[failFirst], func(t *testing.T) {
				// Given
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"GET /sandboxes/source":       jsonHandler(t, sandboxFixture("source")),
					"POST /sandboxes/source/fork": jsonHandler(t, map[string]string{"id": "fork", "status": "pending"}),
					"GET /sandboxes/fork": func(w http.ResponseWriter, r *http.Request) {
						if !failFirst && r.URL.Query().Get("wait") == "" {
							detail := sandboxFixture("fork")
							detail["status"] = "pending"
							writeJSON(t, w, detail)
							return
						}
						w.WriteHeader(status)
					},
				})
				sandbox := newForkTestSource(t, fake)
				// When: a short wait, so a retried 500 spends its budget.
				fork, err := sandbox.Fork(t.Context(), &ForkOptions{Wait: time.Second})
				// Then the accepted handle is returned however the wait ended.
				require.NoError(t, err)
				require.Equal(t, SandboxStateCreating, fork.State())
				require.Equal(t, "source", fork.Info().ForkedFrom)
				count := 4
				if failFirst {
					count = 3
				}
				if status == http.StatusInternalServerError && !failFirst {
					// A transient wait read is retried within the budget (0.2 s, 0.4 s, ...).
					require.Greater(t, len(fake.requests()), count)
				} else {
					require.Len(t, fake.requests(), count)
				}
			})
		}
	}
}

func TestForkSubmissionFailureRemainsAnError(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/source": jsonHandler(t, sandboxFixture("source")),
		"POST /sandboxes/source/fork": func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusConflict)
			writeJSON(t, w, map[string]string{"code": CodeDiskNotReady, "source": "DAYTONA_API", "trace_id": "fork-trace"})
		},
	})
	sandbox := newForkTestSource(t, fake)
	// When
	fork, err := sandbox.Fork(t.Context(), nil)
	// Then
	require.Nil(t, fork)
	require.ErrorIs(t, err, ErrDiskNotReady)
	var detail *Error
	require.ErrorAs(t, err, &detail)
	require.Equal(t, "fork-trace", detail.TraceID)
	require.Len(t, fake.requests(), 2)
}

func TestForkReturnsTerminalObservation(t *testing.T) {
	// Given
	detail := sandboxFixture("fork")
	detail["status"], detail["error"] = "failed", "provisioning failed"
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/source":       jsonHandler(t, sandboxFixture("source")),
		"POST /sandboxes/source/fork": jsonHandler(t, map[string]string{"id": "fork"}),
		"GET /sandboxes/fork":         jsonHandler(t, detail),
	})
	sandbox := newForkTestSource(t, fake)
	// When
	fork, err := sandbox.Fork(t.Context(), nil)
	// Then
	require.NoError(t, err)
	require.Equal(t, SandboxStateError, fork.State())
	require.Equal(t, "provisioning failed", fork.Info().Error)
}

func TestForkReturnsHandleAfterFiniteDetailBudgetExpires(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/source":       jsonHandler(t, sandboxFixture("source")),
		"POST /sandboxes/source/fork": jsonHandler(t, map[string]string{"id": "fork", "status": "pending"}),
		"GET /sandboxes/fork":         func(_ http.ResponseWriter, r *http.Request) { <-r.Context().Done() },
	})
	sandbox := newForkTestSource(t, fake)
	// When
	fork, err := sandbox.Fork(t.Context(), &ForkOptions{Wait: 25 * time.Millisecond})
	// Then
	require.NoError(t, err)
	require.Equal(t, SandboxStateCreating, fork.State())
	require.Len(t, fake.requests(), 3)
}

func TestForkCallerCancellationAfterAcceptanceRemainsAnError(t *testing.T) {
	// Given
	arrived := make(chan struct{})
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/source":       jsonHandler(t, sandboxFixture("source")),
		"POST /sandboxes/source/fork": jsonHandler(t, map[string]string{"id": "fork"}),
		"GET /sandboxes/fork": func(_ http.ResponseWriter, r *http.Request) {
			close(arrived)
			<-r.Context().Done()
		},
	})
	sandbox := newForkTestSource(t, fake)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	result := make(chan error, 1)
	// When
	go func() {
		_, err := sandbox.Fork(ctx, &ForkOptions{Wait: WaitForever})
		result <- err
	}()
	<-arrived
	cancel()
	err := <-result
	// Then
	require.ErrorIs(t, err, context.Canceled)
	require.Len(t, fake.requests(), 3)
}
