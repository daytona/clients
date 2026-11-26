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
		require.Equal(t, 30*time.Second, time.Since(start))
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
