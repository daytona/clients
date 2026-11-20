// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"strconv"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestDockerWait_whenNonterminalStateBecomesReady(t *testing.T) {
	for _, state := range []string{"starting", "deferred", "future"} {
		t.Run(state, func(t *testing.T) {
			// Given
			var polls atomic.Int32
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /docker/status": func(w http.ResponseWriter, _ *http.Request) {
				current := state
				if polls.Add(1) > 1 {
					current = "ready"
				}
				writeJSON(t, w, map[string]string{"owner": "daemon", "state": current})
			}})
			sandbox := dockerVolumeSandbox(t, fake, WithRequestTimeout(time.Nanosecond))
			// When
			result, err := sandbox.Docker().WaitUntilReady(t.Context(), &DockerWaitOptions{Timeout: WaitForever})
			// Then
			require.NoError(t, err)
			require.Equal(t, DockerStateReady, result.State)
			require.Len(t, fake.requests(), 2)
			for _, request := range fake.requests() {
				require.Equal(t, "1000", request.Query.Get("waitMs"))
			}
		})
	}
}

func TestDockerWait_whenTimeoutDefaultOrUnbounded(t *testing.T) {
	for _, timeout := range []time.Duration{0, WaitForever, maxTimerDuration + time.Nanosecond} {
		t.Run(timeout.String(), func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /docker/status": jsonHandler(t, map[string]string{"owner": "daemon", "state": "ready"})})
			sandbox := dockerVolumeSandbox(t, fake)
			// When
			_, err := sandbox.Docker().WaitUntilReady(t.Context(), &DockerWaitOptions{Timeout: timeout})
			// Then
			require.NoError(t, err)
			require.Equal(t, "1000", fake.requests()[0].Query.Get("waitMs"))
		})
	}
}

func TestDockerWait_whenCallerDeadlineExpiresDuringPoll(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /docker/status": func(_ http.ResponseWriter, r *http.Request) { <-r.Context().Done() }})
	sandbox := dockerVolumeSandbox(t, fake)
	ctx, cancel := context.WithTimeout(t.Context(), 50*time.Millisecond)
	defer cancel()
	// When
	_, err := sandbox.Docker().WaitUntilReady(ctx, &DockerWaitOptions{Timeout: WaitForever})
	// Then
	require.ErrorIs(t, err, context.DeadlineExceeded)
	var wait *DockerWaitTimeoutError
	require.NotErrorAs(t, err, &wait)
}

func TestDockerWait_whenLastHoldIsShort(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /docker/status": jsonHandler(t, map[string]string{"owner": "daemon", "state": "ready"})})
	sandbox := dockerVolumeSandbox(t, fake)
	// When
	_, err := sandbox.Docker().WaitUntilReady(t.Context(), &DockerWaitOptions{Timeout: 250 * time.Millisecond})
	// Then
	require.NoError(t, err)
	hold, err := strconv.Atoi(fake.requests()[0].Query.Get("waitMs"))
	require.NoError(t, err)
	require.Positive(t, hold)
	require.LessOrEqual(t, hold, 250)
}

func TestDockerWait_whenClientClosedBeforeCall(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	sandbox := dockerVolumeSandbox(t, fake)
	require.NoError(t, sandbox.client.Close())
	// When
	_, err := sandbox.Docker().WaitUntilReady(t.Context(), nil)
	// Then
	require.ErrorIs(t, err, ErrClientClosed)
	require.Empty(t, fake.requests())
}

func TestDockerStatus_whenActiveRequestCanceled(t *testing.T) {
	// Given
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /docker/status": func(_ http.ResponseWriter, r *http.Request) {
		cancel()
		<-r.Context().Done()
	}})
	sandbox := dockerVolumeSandbox(t, fake)
	// When
	_, err := sandbox.Docker().Status(ctx)
	// Then
	require.ErrorIs(t, err, context.Canceled)
	require.Len(t, fake.requests(), 1)
}
