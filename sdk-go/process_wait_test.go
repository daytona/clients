// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestWaitReadsRecordWhenExecutionTimedOut(t *testing.T) {
	// Given timed_out is a terminal execution deadline, not a wait budget.
	fixture := processFixture("p")
	fixture["state"], fixture["reason"], fixture["exitCode"], fixture["signal"] = "terminal", "timed_out", 124, "SIGTERM"
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
		"GET /processes/p/wait": jsonHandler(t, map[string]string{"reason": "timed_out"}),
		"GET /processes/p":      jsonHandler(t, fixture),
	})
	handle, err := sandbox.Process().Connect("p")
	require.NoError(t, err)
	// When waiting without a caller deadline.
	result, err := handle.Wait(t.Context(), nil)
	// Then the terminal record's real exit and signal are retained.
	require.NoError(t, err)
	require.Equal(t, 124, *result.ExitCode)
	require.Equal(t, ProcessSignalTerminate, result.Signal)
	require.Len(t, fake.requests(), 2)
	require.Equal(t, "30000", fake.requests()[0].Query.Get("timeoutMs"))
}

func TestWaitContinuesWhenLongPollExpiresButProcessRuns(t *testing.T) {
	// Given the first wait window expires before the process.
	calls := 0
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
		"GET /processes/p/wait": func(w http.ResponseWriter, _ *http.Request) {
			calls++
			if calls == 1 {
				writeJSON(t, w, map[string]string{"reason": "timed_out"})
				return
			}
			writeJSON(t, w, map[string]any{"reason": "exited", "exitCode": 3})
		},
		"GET /processes/p": jsonHandler(t, processFixture("p")),
	})
	handle, err := sandbox.Process().Connect("p")
	require.NoError(t, err)
	// When waiting until terminal.
	result, err := handle.Wait(t.Context(), nil)
	// Then it follows the record and starts another long poll.
	require.NoError(t, err)
	require.Equal(t, 3, *result.ExitCode)
	require.Len(t, fake.requests(), 3)
}

func TestWaitStopsRetryingWhenTransportKeepsFailing(t *testing.T) {
	// Given truncated HTTP response bodies, not status-level errors.
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
		"GET /processes/p/wait": func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Content-Length", "50")
			if _, err := w.Write([]byte("{")); err != nil {
				t.Error(err)
			}
		},
	})
	handle, err := sandbox.Process().Connect("p")
	require.NoError(t, err)
	// When every observation fails.
	_, err = handle.Wait(t.Context(), nil)
	// Then the failure streak is bounded to initial plus three retries.
	require.ErrorIs(t, err, ErrConnection)
	require.Len(t, fake.requests(), 4)
}

func TestWaitStopsWhenCallerCancels(t *testing.T) {
	// Given an active long-poll.
	entered := make(chan struct{})
	sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{
		"GET /processes/p/wait": func(_ http.ResponseWriter, r *http.Request) { close(entered); <-r.Context().Done() },
	})
	handle, err := sandbox.Process().Connect("p")
	require.NoError(t, err)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	done := make(chan error, 1)
	go func() { _, waitErr := handle.Wait(ctx, nil); done <- waitErr }()
	<-entered
	// When the caller cancels.
	cancel()
	// Then cancellation remains reachable, not a fabricated timed_out result.
	require.ErrorIs(t, <-done, context.Canceled)
}

func TestWaitPreservesAbsenceWhenServerOmitsOutcome(t *testing.T) {
	// Given a future server does not report a reason, exit code or signal.
	sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{"GET /processes/p/wait": jsonHandler(t, map[string]any{})})
	handle, err := sandbox.Process().Connect("p")
	require.NoError(t, err)
	// When observing the outcome.
	result, err := handle.Wait(t.Context(), nil)
	// Then unknown and nil do not claim success.
	require.NoError(t, err)
	require.Equal(t, ProcessReasonUnknown, result.Reason)
	require.Nil(t, result.ExitCode)
	require.Empty(t, result.Signal)
}
