// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import (
	"encoding/json"
	"strconv"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestLimitCooldown_whenReconcileRunsBeforeRetry(t *testing.T) {
	id := "sbx"
	for _, tc := range []struct {
		name  string
		id    *string
		fresh bool
	}{
		{name: "per_id", id: &id},
		{name: "socket"},
		{name: "queued_subscribe", id: &id, fresh: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given: LIMIT refused an active subscription for two seconds.
			hub := New(t.Context(), Config[Update]{ReconcileInterval: 250 * time.Millisecond})
			defer hub.Close()
			hub.clock, hub.random = newClock(), func() float64 { return 0.5 }
			now := hub.clock.Now()
			s := hub.session(endpointKey{value: "a"}, "a")
			s.entries["sbx"] = &entry[Update]{
				sent: true, fresh: tc.fresh, heardAt: now,
				listeners: map[*listener[Update]]struct{}{{out: make(chan Update, 1), stop: func() bool { return true }}: {}},
			}
			s.dispatch(frame{Type: "error", Code: "LIMIT", ID: tc.id})
			loop := socketLoop[Update]{session: s, reconcile: now.Add(250 * time.Millisecond), sweepAt: now.Add(time.Second)}

			// When: an early reconciliation flushes pending subscriptions.
			later := now.Add(250 * time.Millisecond)
			loop.schedule(later)
			batches := s.pending(later)

			// Then: no subscribe frame escapes the refusal cooldown.
			for _, batch := range batches {
				frames, err := subscriptionFrames(batch.kind, batch.ids)
				require.NoError(t, err)
				require.Empty(t, frames)
			}
		})
	}
}

func TestLimitRetryBatch_whenReconcileCoincidesWithCooldownExpiry(t *testing.T) {
	// Given: more refused IDs than the retry budget, all due together.
	hub := New(t.Context(), Config[Update]{ReconcileInterval: 250 * time.Millisecond})
	defer hub.Close()
	hub.clock, hub.random = newClock(), func() float64 { return 0.5 }
	now := hub.clock.Now()
	s := hub.session(endpointKey{value: "a"}, "a")
	for i := range 1001 {
		s.entries[strconv.Itoa(i)] = &entry[Update]{
			sent: true, heardAt: now,
			listeners: map[*listener[Update]]struct{}{{out: make(chan Update, 1), stop: func() bool { return true }}: {}},
		}
	}
	s.dispatch(frame{Type: "error", Code: "LIMIT"})
	loop := socketLoop[Update]{session: s, reconcile: now.Add(250 * time.Millisecond), sweepAt: now.Add(time.Second)}

	// When: a reconcile and the refusal retry run at the same instant.
	later := now.Add(2 * time.Second)
	loop.schedule(later)
	batches := s.pending(later)

	// Then: only one 1,000-ID retry frame is emitted; excess IDs remain delayed.
	sent := 0
	for _, batch := range batches {
		frames, err := subscriptionFrames(batch.kind, batch.ids)
		require.NoError(t, err)
		for _, payload := range frames {
			var wire clientFrame
			require.NoError(t, json.Unmarshal(payload, &wire))
			require.Equal(t, "subscribe", wire.Type)
			require.Len(t, wire.IDs, 1000)
			sent += len(wire.IDs)
		}
	}
	require.Equal(t, 1000, sent)
}
