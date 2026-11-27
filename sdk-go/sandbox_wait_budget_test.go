// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestWait_whenBudgetExpires_returnsRequestTimeout(t *testing.T) {
	for _, budget := range []time.Duration{0, time.Second, time.Nanosecond} {
		t.Run(budget.String(), func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given: immediate nonterminal reads exercise minimum poll spacing.
				sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
					detail := sandboxFixture("sbx")
					detail["status"] = "booting"
					writeJSON(t, w, detail)
				})
				began := time.Now()
				// When
				err := sandbox.Wait(t.Context(), &WaitOptions{Timeout: budget})
				// Then
				require.ErrorIs(t, err, ErrRequestTimeout)
				require.NotErrorIs(t, err, ErrClientTimeout)
				expected := budget
				if expected == 0 {
					expected = time.Minute
				}
				require.Equal(t, expected, time.Since(began))
			})
		})
	}
}

func TestWait_whenReadStalls_capsSliceAndDeadline(t *testing.T) {
	for _, test := range []struct {
		budget, root, elapsed, caller time.Duration
		wire                          string
		clientTimeout                 bool
	}{
		{50 * time.Millisecond, time.Minute, 50 * time.Millisecond, 0, "50", false},
		{31 * time.Second, time.Minute, 31 * time.Second, 0, "30000", false},
		// A stalled slice is retried; the wait deadline then wraps that client timeout.
		{time.Minute, time.Minute, time.Minute, 0, "24800", true},
		{WaitForever, time.Millisecond, 40 * time.Second, 40 * time.Second, "4800", true},
		{time.Nanosecond, time.Minute, time.Nanosecond, 0, "1", false},
	} {
		t.Run(test.budget.String(), func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given
				var hold string
				sandbox := virtualWaitSandbox(t, func(_ http.ResponseWriter, r *http.Request) {
					hold = r.URL.Query().Get("timeout_ms")
					<-r.Context().Done()
				})
				sandbox.client.config.timeout = test.root
				ctx := t.Context()
				if test.caller > 0 {
					var cancel context.CancelFunc
					ctx, cancel = context.WithTimeout(ctx, test.caller)
					defer cancel()
				}
				began := time.Now()
				// When
				err := sandbox.Wait(ctx, &WaitOptions{Timeout: test.budget})
				// Then: only the SDK wait deadline converts an earlier client timer.
				require.ErrorIs(t, err, ErrRequestTimeout)
				require.Equal(t, test.elapsed, time.Since(began))
				require.Equal(t, test.wire, hold)
				if test.clientTimeout {
					require.ErrorIs(t, err, ErrClientTimeout)
				} else {
					require.NotErrorIs(t, err, ErrClientTimeout)
				}
			})
		})
	}
}

func TestWait_whenUnbounded_keepsFiniteSlices(t *testing.T) {
	for _, budget := range []time.Duration{WaitForever, maxTimerDuration + time.Nanosecond} {
		synctest.Test(t, func(t *testing.T) {
			// Given
			var reads int
			sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, r *http.Request) {
				reads++
				if r.URL.Query().Get("timeout_ms") != "30000" {
					t.Error("unbounded wait did not use a finite 30-second slice")
				}
				detail := sandboxFixture("sbx")
				if reads <= 2 {
					timer := time.NewTimer(30 * time.Second)
					defer timer.Stop()
					<-timer.C
					detail["status"] = "booting"
				}
				writeJSON(t, w, detail)
			})
			// When
			err := sandbox.Wait(t.Context(), &WaitOptions{Timeout: budget})
			// Then
			require.NoError(t, err)
			require.Equal(t, 3, reads)
		})
	}
}

func TestWait_whenContextDeadlineBoundsForever_preservesDeadlineCause(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		sandbox := virtualWaitSandbox(t, func(_ http.ResponseWriter, r *http.Request) { <-r.Context().Done() })
		ctx, cancel := context.WithTimeout(t.Context(), time.Second)
		defer cancel()
		// When
		err := sandbox.Wait(ctx, &WaitOptions{Timeout: WaitForever})
		// Then
		require.ErrorIs(t, err, context.DeadlineExceeded)
	})
}

func TestWait_whenClientCloses_stopsEventWait(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		sandbox := virtualWaitSandbox(t, func(http.ResponseWriter, *http.Request) {
			t.Error("closed client must not probe")
		})
		source := &waitTestSource{events: make(chan stateObservation)}
		sandbox.client.stateSource = source
		done := make(chan error, 1)
		go func() { done <- sandbox.Wait(t.Context(), &WaitOptions{Timeout: WaitForever}) }()
		synctest.Wait()
		// When
		require.NoError(t, sandbox.client.Close())
		// Then
		require.ErrorIs(t, <-done, ErrClientClosed)
		require.EqualValues(t, 1, source.released.Load())
	})
}

func TestWait_whenProbeReachesReserve_joinsBeforeFinalRead(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		var reads int
		var finished bool
		var finalReadAt time.Duration
		began := time.Now()
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, r *http.Request) {
			reads++
			if reads == 1 {
				<-r.Context().Done()
				finished = true
				return
			}
			if !finished {
				t.Error("final read overlapped the probe")
			}
			finalReadAt = time.Since(began)
			writeJSON(t, w, sandboxFixture("sbx"))
		})
		sandbox.client.stateSource = &waitTestSource{events: make(chan stateObservation)}
		// When
		err := sandbox.Wait(t.Context(), &WaitOptions{Timeout: 8 * time.Second})
		// Then: the probe starts at 3 s and must finish at the 6 s share boundary.
		require.NoError(t, err)
		require.Equal(t, 6*time.Second, finalReadAt)
		require.Equal(t, 2, reads)
	})
}

func TestWaitExpired_reportsTheCallersDeadlineEvenBeforeItsTimerFires(t *testing.T) {
	// Given a wait whose binding deadline is the caller's context deadline,
	// observed when that instant passed but before the context's own timer
	// marked it done (the race the scheduler can produce).
	sandbox := virtualWaitSandbox(t, func(http.ResponseWriter, *http.Request) {})
	w, err := sandbox.newWait([]SandboxState{SandboxStateStarted})
	require.NoError(t, err)
	w.deadline, w.contextDeadline = time.Now().Add(-time.Millisecond), true

	// When the wait checks its deadline with a context that is not done yet
	err = w.check(t.Context())

	// Then the error carries the caller's deadline (a client timeout), not the
	// SDK's own wait timeout
	require.ErrorIs(t, err, context.DeadlineExceeded)
	require.ErrorIs(t, err, ErrClientTimeout)
}

func TestWaitExpired_reportsTheSDKTimeoutForItsOwnBudget(t *testing.T) {
	// Given a wait bound by its own budget
	sandbox := virtualWaitSandbox(t, func(http.ResponseWriter, *http.Request) {})
	w, err := sandbox.newWait([]SandboxState{SandboxStateStarted})
	require.NoError(t, err)
	w.deadline, w.contextDeadline = time.Now().Add(-time.Millisecond), false

	// When the budget has passed
	err = w.check(t.Context())

	// Then the error is the SDK's wait timeout
	require.ErrorIs(t, err, ErrRequestTimeout)
	require.NotErrorIs(t, err, context.DeadlineExceeded)
}
