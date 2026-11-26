// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"fmt"
	"testing"
	"testing/synctest"
	"time"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestRetrySandboxTransition_waitsBeforeRepeating_whenSettling(t *testing.T) {
	for _, seconds := range []float64{0, 3, 99} {
		t.Run(fmt.Sprint(seconds), func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given
				calls := 0
				start := time.Now()
				// When
				err := retrySandboxTransition(t.Context(), func() error {
					calls++
					if calls == 1 {
						return &api.APIError{Code: "SANDBOX_TRANSITION_IN_PROGRESS", Body: map[string]any{"retry_after_seconds": seconds}}
					}
					return nil
				})
				// Then
				require.NoError(t, err)
				require.Equal(t, 2, calls)
				require.Equal(t, time.Duration(min(max(seconds, 1), 5)*float64(time.Second)), time.Since(start))
			})
		})
	}
}

func TestRetrySandboxTransition_returnsLastError_whenBudgetExpires(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		calls := 0
		var last *api.APIError
		start := time.Now()
		// When
		err := retrySandboxTransition(t.Context(), func() error {
			calls++
			last = &api.APIError{Code: "SANDBOX_TRANSITION_IN_PROGRESS", Body: map[string]any{"retry_after_seconds": float64(4)}}
			return last
		})
		// Then
		require.Same(t, last, err)
		require.Equal(t, 8, calls)
		// No pause precedes a retry the window could not admit (28 s + 4 s).
		require.Equal(t, 28*time.Second, time.Since(start))
	})
}

func TestRetrySandboxTransition_usesDefaultDelay_whenHintIsFractional(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		calls := 0
		start := time.Now()
		// When
		err := retrySandboxTransition(t.Context(), func() error {
			calls++
			if calls == 1 {
				return &api.APIError{Code: "SANDBOX_TRANSITION_IN_PROGRESS", Body: map[string]any{"retry_after_seconds": 2.5}}
			}
			return nil
		})
		// Then
		require.NoError(t, err)
		require.Equal(t, time.Second, time.Since(start))
	})
}

func TestRetrySandboxTransition_returnsImmediately_whenOtherConflict(t *testing.T) {
	// Given
	refusal := &api.APIError{Status: 409, Code: "CONFLICT"}
	calls := 0
	// When
	err := retrySandboxTransition(t.Context(), func() error {
		calls++
		return refusal
	})
	// Then
	require.Same(t, refusal, err)
	require.Equal(t, 1, calls)
}

func TestRetrySandboxTransition_interruptsDelay_whenCancelled(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		ctx, cancel := context.WithCancel(t.Context())
		defer cancel()
		calls := 0
		go func() {
			synctest.Wait()
			cancel()
		}()
		// When
		err := retrySandboxTransition(ctx, func() error {
			calls++
			return &api.APIError{Code: "SANDBOX_TRANSITION_IN_PROGRESS"}
		})
		// Then
		require.ErrorIs(t, err, context.Canceled)
		require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
		require.Equal(t, 1, calls)
	})
}

func TestRetrySandboxTransition_stopsDelay_whenCallerDeadlineExpires(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		ctx, cancel := context.WithTimeout(t.Context(), 250*time.Millisecond)
		defer cancel()
		start := time.Now()
		// When
		err := retrySandboxTransition(ctx, func() error {
			return &api.APIError{Code: "SANDBOX_TRANSITION_IN_PROGRESS"}
		})
		// Then
		require.ErrorIs(t, err, context.DeadlineExceeded)
		require.Equal(t, cerr.ExitTimeout, cerr.ExitCode(err))
		require.Equal(t, 250*time.Millisecond, time.Since(start))
	})
}

func TestRetrySandboxTransition_returnsContextExit_whenAlreadyEnded(t *testing.T) {
	for _, deadline := range []bool{false, true} {
		t.Run(fmt.Sprint(deadline), func(t *testing.T) {
			// Given
			timeout := time.Hour
			wantErr, wantExit := context.Canceled, cerr.ExitInterrupted
			if deadline {
				timeout = -time.Second
				wantErr, wantExit = context.DeadlineExceeded, cerr.ExitTimeout
			}
			ctx, cancel := context.WithTimeout(t.Context(), timeout)
			defer cancel()
			cancel()
			calls := 0
			// When
			err := retrySandboxTransition(ctx, func() error {
				calls++
				return nil
			})
			// Then
			require.ErrorIs(t, err, wantErr)
			require.Equal(t, wantExit, cerr.ExitCode(err))
			require.Zero(t, calls)
		})
	}
}

func TestRetrySandboxTransition_preservesRequestBudget_whenAttemptIsAdmitted(t *testing.T) {
	for _, tc := range []struct {
		name          string
		firstDuration time.Duration
		retry         bool
		accepted      bool
		wantCalls     int
		wantDuration  time.Duration
	}{
		{"first attempt", 31 * time.Second, false, true, 1, 31 * time.Second},
		{"accepted retry", 24 * time.Second, true, true, 2, 31 * time.Second},
		{"refused retry", 24 * time.Second, true, false, 2, 31 * time.Second},
	} {
		t.Run(tc.name, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given a first request or an admitted retry outlasting the retry window.
				calls := 0
				refusal := &api.APIError{Code: "SANDBOX_TRANSITION_IN_PROGRESS", Body: map[string]any{"retry_after_seconds": float64(5)}}
				start := time.Now()
				// When each admitted POST uses its ordinary request budget.
				err := retrySandboxTransition(t.Context(), func() error {
					calls++
					if calls == 1 {
						<-time.After(tc.firstDuration)
						if tc.retry {
							return refusal
						}
					} else {
						<-time.After(2 * time.Second)
					}
					if !tc.accepted {
						return refusal
					}
					return nil
				})
				// Then acceptance survives and a late refusal admits no further retry.
				if tc.accepted {
					require.NoError(t, err)
				} else {
					require.Same(t, refusal, err)
				}
				require.Equal(t, tc.wantCalls, calls)
				require.Equal(t, tc.wantDuration, time.Since(start))
			})
		})
	}
}
