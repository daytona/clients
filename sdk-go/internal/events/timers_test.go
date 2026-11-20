// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import (
	"context"
	"errors"
	"fmt"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/stretchr/testify/require"
)

func TestStaleSweepResubscribesOnlySilentIDsWhenSocketIsBusy(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	first, release := f.hub.Watch(t.Context(), "silent", "a")
	defer release()
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")
	_, other := f.hub.Watch(t.Context(), "busy", "a")
	defer other()
	f.clock.advance(flushDelay)
	nextFrame(t, p, "subscribe")
	send(t, p, `{"type":"snapshot","id":"silent","status":"pending","version":1}`)
	receive(t, first)

	// When
	f.clock.advance(2 * time.Second)
	send(t, p, `{"type":"event","id":"busy","status":"running","version":4}`)
	send(t, p, `{"type":"ping"}`)
	nextFrame(t, p, "pong")
	f.clock.advance(time.Second)

	// Then
	require.Equal(t, []subscription{{ID: "silent", Known: true}}, subscriptions(t, nextFrame(t, p, "subscribe")))
}

func TestSweepLimitsWorkWhenMoreThan1500IDsAreSilent(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	connect := f.hub.config.Connect
	gate := make(chan struct{})
	f.hub.config.Connect = func(ctx context.Context, region string) (Connection, error) {
		select {
		case <-ctx.Done():
			return Connection{}, ctx.Err()
		case <-gate:
			return connect(ctx, region)
		}
	}
	for i := range 1601 {
		f.hub.Watch(t.Context(), fmt.Sprintf("sbx-%04d", i), "a")
	}
	close(gate)
	p := receive(t, f.accepted)
	require.Len(t, nextFrame(t, p, "subscribe").IDs, 1000)
	require.Len(t, nextFrame(t, p, "subscribe").IDs, 601)

	// When
	f.clock.advance(3 * time.Second)

	// Then
	require.Len(t, nextFrame(t, p, "subscribe").IDs, 1000)
	require.Len(t, nextFrame(t, p, "subscribe").IDs, 500)
	f.clock.advance(time.Second)
	require.Len(t, nextFrame(t, p, "subscribe").IDs, 101)
}

func TestLimitRetriesRefusedIDWhenRetryTimerExpires(t *testing.T) {
	for _, id := range []string{`"sbx"`, `null`} {
		t.Run(id, func(t *testing.T) {
			// Given
			f := newFixture(t, 0)
			_, release := f.hub.Watch(t.Context(), "sbx", "a")
			defer release()
			p := receive(t, f.accepted)
			nextFrame(t, p, "subscribe")
			send(t, p, `{"type":"error","id":`+id+`,"code":"LIMIT"}`)
			send(t, p, `{"type":"ping"}`)
			nextFrame(t, p, "pong")

			// When
			f.clock.advance(refusedDelay)

			// Then
			require.Equal(t, []subscription{{ID: "sbx"}}, subscriptions(t, nextFrame(t, p, "subscribe")))
		})
	}
}

func TestReconnectResnapshotsKnownIDsWhenSocketCloses(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	ch, release := f.hub.Watch(t.Context(), "sbx", "a")
	defer release()
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")
	send(t, p, `{"type":"snapshot","id":"sbx","status":"running","version":5}`)
	receive(t, ch)

	// When
	require.NoError(t, p.conn.Close(websocket.StatusGoingAway, "restart"))
	awaitTimer(t, f.clock, f.clock.Now().Add(125*time.Millisecond))
	f.clock.advance(125 * time.Millisecond)
	reconnected := receive(t, f.accepted)

	// Then
	require.Equal(t, []subscription{{ID: "sbx", Known: true}},
		subscriptions(t, nextFrame(t, reconnected, "subscribe")))
	send(t, reconnected, `{"type":"event","id":"sbx","status":"running","version":5}`)
	send(t, reconnected, `{"type":"snapshot","id":"sbx","status":"stopped","version":6}`)
	require.Equal(t, "stopped", receive(t, ch).Status)
}

func TestRetryAfterBoundsReconnectWhenMintIsRateLimited(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	connect := f.hub.config.Connect
	calls := make(chan struct{}, 4)
	count := 0
	f.hub.config.Connect = func(ctx context.Context, region string) (Connection, error) {
		count++
		calls <- struct{}{}
		if count == 1 {
			return Connection{RetryAfter: 12 * time.Second}, errors.New("HTTP 429")
		}
		return connect(ctx, region)
	}
	_, release := f.hub.Watch(t.Context(), "sbx", "a")
	defer release()
	receive(t, calls)
	awaitTimer(t, f.clock, f.clock.Now().Add(12*time.Second))

	// When
	f.clock.advance(11 * time.Second)

	// Then
	select {
	case <-calls:
		t.Fatal("retried before Retry-After")
	default:
	}
	f.clock.advance(time.Second)
	receive(t, calls)
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")
}

func TestReconcileIncludesRecentlyHeardIDsWhenIntervalExpires(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	f.hub.config.ReconcileInterval = 2 * time.Second
	ch, release := f.hub.Watch(t.Context(), "sbx", "a")
	defer release()
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")
	f.clock.advance(time.Second)
	send(t, p, `{"type":"event","id":"sbx","status":"running","version":1}`)
	receive(t, ch)

	// When
	f.clock.advance(time.Second)

	// Then
	require.Equal(t, []subscription{{ID: "sbx", Known: true}}, subscriptions(t, nextFrame(t, p, "subscribe")))
}

func awaitTimer(t *testing.T, c *manualClock, at time.Time) {
	t.Helper()
	for {
		if receive(t, c.armed).Equal(at) {
			return
		}
	}
}
