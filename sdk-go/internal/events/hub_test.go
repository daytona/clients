// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestWatchDeliversLatestStateWhenFramesArrive(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	ch, release := f.hub.Watch(t.Context(), "sbx", "region-a")
	defer release()
	p := receive(t, f.accepted)
	require.Equal(t, []subscription{{ID: "sbx"}}, subscriptions(t, nextFrame(t, p, "subscribe")))

	// When
	send(t, p, `{"type":"snapshot","id":"sbx","status":"failed","version":3,"error":"boot failed"}`)

	// Then
	require.Equal(t, Update{Status: "failed", Reason: "boot failed", At: f.clock.Now()}, receive(t, ch))
}

func TestFramesSkipUnknownAndMalformedWhenFollowedByValidState(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	ch, release := f.hub.Watch(t.Context(), "sbx", "a")
	defer release()
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")

	// When
	for _, payload := range []string{
		`not json`, `null`, `[]`, `{"type":"future","version":{}}`, `{"type":"event","id":"sbx"}`,
		`{"type":"event","id":"sbx","version":null}`, `{"type":"error","id":"sbx","code":"FUTURE"}`,
		`{"type":"event","id":"another","status":"deleted","version":10}`,
		`{"type":"snapshot","id":"sbx","status":"future-state","version":1}`,
	} {
		send(t, p, payload)
	}

	// Then
	require.Equal(t, "future-state", receive(t, ch).Status)
}

func TestVersionsDiscardOlderWhenEqualVersionSnapshotArrives(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	ch, release := f.hub.Watch(t.Context(), "sbx", "a")
	defer release()
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")
	send(t, p, `{"type":"event","id":"sbx","status":"running","version":5}`)
	require.Equal(t, "running", receive(t, ch).Status)

	// When
	send(t, p, `{"type":"snapshot","id":"sbx","status":"pending","version":3}`)
	send(t, p, `{"type":"snapshot","id":"sbx","status":"running","version":5}`)

	// Then
	require.Equal(t, "running", receive(t, ch).Status)
}

func TestWatchSharesSocketAndCacheWhenSameIDIsWatched(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	first, releaseFirst := f.hub.Watch(t.Context(), "sbx", "a")
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")
	send(t, p, `{"type":"snapshot","id":"sbx","status":"running","version":2}`)
	receive(t, first)

	// When
	second, releaseSecond := f.hub.Watch(t.Context(), "sbx", "a")
	defer releaseSecond()
	releaseFirst()

	// Then
	require.Equal(t, "running", receive(t, second).Status)
	send(t, p, `{"type":"event","id":"sbx","status":"stopped","version":3}`)
	require.Equal(t, "stopped", receive(t, second).Status)
}

func TestWatchUnsubscribesWhenContextCancelled(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	ch, release := f.hub.Watch(ctx, "sbx", "a")
	defer release()
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")

	// When
	cancel()
	expectClosed(t, ch)
	f.clock.advance(flushDelay)

	// Then
	unsub := nextFrame(t, p, "unsubscribe")
	require.JSONEq(t, `"sbx"`, string(unsub.IDs[0]))
}

func TestCloseJoinsSocketsWhenMultipleRegionsActive(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	a, _ := f.hub.Watch(t.Context(), "a", "first")
	b, _ := f.hub.Watch(t.Context(), "b", "second")
	first, second := receive(t, f.accepted), receive(t, f.accepted)
	nextFrame(t, first, "subscribe")
	nextFrame(t, second, "subscribe")

	// When
	f.hub.Close()

	// Then
	expectClosed(t, a)
	expectClosed(t, b)
	expectClosed(t, first.done)
	expectClosed(t, second.done)
	ch, release := f.hub.Watch(t.Context(), "new", "a")
	release()
	require.Nil(t, ch)
}

func TestIdleCloseClosesSocketWhenLastWatchReleased(t *testing.T) {
	// Given
	f := newFixture(t, 10*time.Second)
	_, release := f.hub.Watch(t.Context(), "sbx", "a")
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")

	// When
	release()
	f.clock.advance(10 * time.Second)

	// Then
	expectClosed(t, p.done)
}

func TestWatchFallsBackWhenIDErrorArrives(t *testing.T) {
	for _, code := range []string{"NOT_FOUND", "MALFORMED"} {
		t.Run(code, func(t *testing.T) {
			// Given
			f := newFixture(t, 0)
			ch, release := f.hub.Watch(t.Context(), "sbx", "a")
			defer release()
			p := receive(t, f.accepted)
			nextFrame(t, p, "subscribe")

			// When
			send(t, p, `{"type":"error","id":"sbx","code":"`+code+`","was_known":false}`)

			// Then
			expectClosed(t, ch)
		})
	}
}
