// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestLearnSharesRegionalSocketWhenDestinationMatches(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	f.hub.Learn("a", "destination")
	f.hub.Learn("b", "destination")
	first, releaseA := f.hub.Watch(t.Context(), "first", "a")
	defer releaseA()
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")

	// When
	second, releaseB := f.hub.Watch(t.Context(), "second", "b")
	defer releaseB()
	f.clock.advance(flushDelay)

	// Then
	require.Equal(t, []subscription{{ID: "second"}}, subscriptions(t, nextFrame(t, p, "subscribe")))
	send(t, p, `{"type":"snapshot","id":"first","status":"running","version":1}`)
	send(t, p, `{"type":"snapshot","id":"second","status":"paused","version":1}`)
	require.Equal(t, "running", receive(t, first).Status)
	require.Equal(t, "paused", receive(t, second).Status)
}

func TestNotFoundRetriesRegionWhenLearnedDestinationIsStale(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	f.hub.Learn("a", "destination")
	ch, release := f.hub.Watch(t.Context(), "sbx", "a")
	defer release()
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")

	// When
	send(t, p, `{"type":"error","id":"sbx","code":"NOT_FOUND","was_known":true}`)
	retry := receive(t, f.accepted)
	nextFrame(t, retry, "subscribe")
	send(t, retry, `{"type":"snapshot","id":"sbx","status":"running","version":2}`)

	// Then
	require.Equal(t, "running", receive(t, ch).Status)
	release()
	expectClosed(t, ch)
}

func TestLearnMovesExistingWatchWhenDestinationChanges(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	ch, release := f.hub.Watch(t.Context(), "sbx", "a")
	defer release()
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")
	f.hub.Learn("a", "old-destination")

	// When
	f.hub.Learn("a", "new-destination")
	target := receive(t, f.accepted)
	nextFrame(t, target, "subscribe")
	send(t, target, `{"type":"snapshot","id":"sbx","status":"stopped","version":1}`)

	// Then
	require.Equal(t, "stopped", receive(t, ch).Status)
	expectClosed(t, p.done)
	release()
	expectClosed(t, ch)
}

func TestLearnKeepsOtherRegionWhenSharedDestinationChanges(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	f.hub.Learn("a", "shared")
	f.hub.Learn("b", "shared")
	other, releaseB := f.hub.Watch(t.Context(), "other", "b")
	defer releaseB()
	original := receive(t, f.accepted)
	nextFrame(t, original, "subscribe")
	moving, releaseA := f.hub.Watch(t.Context(), "moving", "a")
	defer releaseA()
	f.clock.advance(flushDelay)
	nextFrame(t, original, "subscribe")

	// When
	f.hub.Learn("a", "replacement")
	replacement := receive(t, f.accepted)
	require.Equal(t, []subscription{{ID: "moving"}}, subscriptions(t, nextFrame(t, replacement, "subscribe")))

	// Then
	send(t, original, `{"type":"event","id":"other","status":"paused","version":1}`)
	send(t, replacement, `{"type":"snapshot","id":"moving","status":"running","version":1}`)
	require.Equal(t, "paused", receive(t, other).Status)
	require.Equal(t, "running", receive(t, moving).Status)
}

func TestRegionalNameDoesNotAliasOpaqueDestination(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	f.hub.Learn("a", "b")
	_, releaseA := f.hub.Watch(t.Context(), "first", "a")
	defer releaseA()
	destination := receive(t, f.accepted)
	nextFrame(t, destination, "subscribe")

	// When
	ch, releaseB := f.hub.Watch(t.Context(), "second", "b")
	defer releaseB()

	// Then: an opaque destination named "b" is not the region named "b".
	regional := receive(t, f.accepted)
	require.Equal(t, []subscription{{ID: "second"}}, subscriptions(t, nextFrame(t, regional, "subscribe")))
	send(t, regional, `{"type":"snapshot","id":"second","status":"running","version":1}`)
	require.Equal(t, "running", receive(t, ch).Status)
}
