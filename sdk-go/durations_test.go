// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestDurations_whenConvertingWireUnits(t *testing.T) {
	for _, test := range []struct {
		duration              time.Duration
		seconds, milliseconds int64
	}{
		{NoWait, 0, 0},
		{WaitForever, 0, 0},
		{NoTimeout, 0, 0},
		{0, 0, 0},
		{time.Nanosecond, 1, 1},
		{time.Millisecond + time.Nanosecond, 1, 2},
		{time.Second, 1, 1000},
		{time.Second + time.Millisecond, 2, 1001},
	} {
		// Given/When/Then
		require.Equal(t, test.seconds, wireSeconds(test.duration))
		require.Equal(t, test.milliseconds, wireMilliseconds(test.duration))
	}
	require.Equal(t, maxTimerDuration, boundedTimeout(maxTimerDuration))
	require.Zero(t, boundedTimeout(maxTimerDuration+1))
}

func TestRequestScope_whenProgressContinuesPastTotalBudget(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		client, err := New(WithAPIKey("key"))
		require.NoError(t, err)
		defer func() { require.NoError(t, client.Close()) }()
		_, scope := newRequestScope(t.Context(), client, time.Second)
		defer scope.close()
		scope.kick()
		// When: idle progress extends the same request beyond three total seconds.
		for range 6 {
			timer := time.NewTimer(500 * time.Millisecond)
			<-timer.C
			scope.kick()
		}
		// Then
		require.NoError(t, scope.ctx.Err())
	})
}

func TestRequestScope_whenOpeningTimerIsDisarmed(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		client, err := New(WithAPIKey("key"))
		require.NoError(t, err)
		defer func() { require.NoError(t, client.Close()) }()
		_, scope := newRequestScope(t.Context(), client, time.Second)
		defer scope.close()
		scope.kick()
		// When: response headers arrive and a stream has no idle bound.
		scope.pause()
		timer := time.NewTimer(2 * time.Second)
		<-timer.C
		// Then
		require.NoError(t, scope.ctx.Err())
		require.NoError(t, client.Close())
		synctest.Wait()
		require.ErrorIs(t, context.Cause(scope.ctx), ErrClientClosed)
	})
}
