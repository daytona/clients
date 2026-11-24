// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import (
	"strconv"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestKnownSubscriptions_whenHistoryExceedsCapacity(t *testing.T) {
	// Given: fill history, refreshing the first ID so the second is oldest.
	hub := New(t.Context(), Config[Update]{})
	defer hub.Close()
	for i := range 10000 {
		hub.remember(strconv.Itoa(i))
	}
	hub.remember("0")
	hub.remember("5000")
	hub.remember("new")
	hub.remember("new")
	now := hub.clock.Now()
	session := &session[Update]{hub: hub, flushAt: now, entries: make(map[string]*entry[Update])}
	for _, id := range []string{"0", "1", "5000", "9999", "new"} {
		session.entries[id] = &entry[Update]{
			fresh: true, listeners: map[*listener[Update]]struct{}{{}: {}},
		}
	}
	// When: subscription hints reflect the bounded, least-recently-seen history.
	batches := session.pending(now)
	// Then
	known := make(map[string]bool)
	for _, batch := range batches {
		for _, item := range batch.ids {
			known[item.ID] = item.Known
		}
	}
	require.Equal(t, map[string]bool{"0": true, "1": false, "5000": true, "9999": true, "new": true}, known)
	require.Len(t, hub.known, 10000)
}

func BenchmarkRememberAtCapacity(b *testing.B) {
	hub := New(b.Context(), Config[Update]{})
	defer hub.Close()
	for i := range 10000 {
		hub.remember(strconv.Itoa(i))
	}
	ids := make([]string, 10001)
	for i := range ids {
		ids[i] = strconv.Itoa(i + 10000)
	}
	index := 0
	for b.Loop() {
		hub.remember(ids[index])
		index = (index + 1) % len(ids)
	}
}
