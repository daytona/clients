// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import (
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestWatchLiveness_whenAnyFrameArrives_updatesSharedSocketTime(t *testing.T) {
	for name, payload := range map[string]string{
		"snapshot": `{"type":"snapshot","id":"other","status":"pending","version":1}`,
		"event":    `{"type":"event","id":"other","status":"running","version":2}`,
		"error":    `{"type":"error","id":null,"code":"LIMIT"}`,
		"ping":     `{"type":"ping"}`,
		"pong":     `{"type":"pong"}`,
	} {
		t.Run(name, func(t *testing.T) {
			// Given: an idle subscription; no matching state observation is sent.
			f := newFixture(t, 0)
			_, release, activity := f.hub.WatchWithLiveness(t.Context(), "sbx", "a")
			defer release()
			p := receive(t, f.accepted)
			nextFrame(t, p, "subscribe")
			registered := activity.RegisteredAt
			f.clock.advance(time.Second)
			awaitTimer(t, f.clock, f.clock.Now().Add(time.Second))

			// When: await the socket loop's next timer as the processing barrier.
			send(t, p, payload)
			awaitTimer(t, f.clock, f.clock.Now().Add(time.Second))

			// Then: socket-wide frames count without moving the watch's start.
			require.Equal(t, f.clock.Now(), activity.Frame().LastFrameAt)
			require.Equal(t, registered, activity.RegisteredAt)
			require.Equal(t, time.Second, activity.Frame().LastFrameAt.Sub(registered))
		})
	}
}

func TestWatchLiveness_whenRoutedToAnotherSocket_followsListener(t *testing.T) {
	// Given: a watch on a socket whose opaque destination will change.
	f := newFixture(t, 0)
	_, release, activity := f.hub.WatchWithLiveness(t.Context(), "sbx", "a")
	defer release()
	original := receive(t, f.accepted)
	nextFrame(t, original, "subscribe")
	f.hub.Learn("a", "old")
	before := activity.Frame()

	// When
	f.hub.Learn("a", "new")
	replacement := receive(t, f.accepted)
	nextFrame(t, replacement, "subscribe")
	after := activity.Frame()

	// Then: even equal timestamps cannot hide a socket replacement during a GET.
	require.Equal(t, before.LastFrameAt, after.LastFrameAt)
	require.NotSame(t, before.connection, after.connection)
}

func TestWatchLiveness_whenSocketPredatesWatch_recordsFreshRegistration(t *testing.T) {
	// Given: another waiter has held the shared socket for one second.
	f := newFixture(t, 0)
	_, release := f.hub.Watch(t.Context(), "other", "a")
	defer release()
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")
	f.clock.advance(time.Second)
	awaitTimer(t, f.clock, f.clock.Now().Add(time.Second))

	// When
	_, done, activity := f.hub.WatchWithLiveness(t.Context(), "sbx", "a")
	defer done()

	// Then: the new watch cannot inherit the old socket's quiet budget.
	require.Equal(t, f.clock.Now(), activity.RegisteredAt)
	require.Equal(t, time.Second, activity.RegisteredAt.Sub(activity.Frame().LastFrameAt))
}

func TestWatchLiveness_whenJitterSampleChanges_scalesDelayWithinTenPercent(t *testing.T) {
	for _, sample := range []float64{0, 0.5, 1} {
		// Given: the same injectable source used by reconciliation and probes.
		hub := New(t.Context(), Config[Update]{})
		defer hub.Close()
		hub.random = func() float64 { return sample }

		// When
		delay := hub.jitter(10 * time.Second)

		// Then
		require.Equal(t, time.Duration(9+2*sample)*time.Second, delay)
	}
}
