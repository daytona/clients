// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestNotFoundDeliversTerminalUpdate_whenSandboxWasKnown(t *testing.T) {
	for _, tc := range []struct {
		name     string
		observed string
		wasKnown string
		buffered bool
	}{
		{name: "server_known", wasKnown: "true"},
		{name: "local_snapshot", observed: "snapshot", wasKnown: "false"},
		{name: "local_event", observed: "event", wasKnown: "false"},
		{name: "buffered_snapshot", observed: "snapshot", wasKnown: "false", buffered: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given: a watched sandbox known to the server or from a local frame.
			f := newFixture(t, 0)
			ch, release := f.hub.Watch(t.Context(), "sbx", "a")
			defer release()
			p := receive(t, f.accepted)
			nextFrame(t, p, "subscribe")
			if tc.observed != "" {
				send(t, p, `{"type":"`+tc.observed+`","id":"sbx","status":"pending","version":1}`)
				if !tc.buffered {
					receive(t, ch)
				}
			}

			// When: disappearance follows the state, possibly with a stale flag.
			send(t, p, `{"type":"error","id":"sbx","code":"NOT_FOUND","was_known":`+tc.wasKnown+`}`)
			send(t, p, `{"type":"ping"}`)
			nextFrame(t, p, "pong")

			// Then: a terminal result replaces buffered state before closure.
			require.Equal(t, Update{NotFound: true, At: f.clock.Now()}, receive(t, ch))
			expectClosed(t, ch)
		})
	}
}

func TestNotFoundUsesHistory_whenPreviousWatchObservedSandbox(t *testing.T) {
	// Given: an earlier watch observed the sandbox, then released it.
	f := newFixture(t, 0)
	previous, release := f.hub.Watch(t.Context(), "sbx", "a")
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")
	send(t, p, `{"type":"snapshot","id":"sbx","status":"pending","version":1}`)
	receive(t, previous)
	release()
	ch, done := f.hub.Watch(t.Context(), "sbx", "a")
	defer done()
	f.clock.advance(flushDelay)
	require.Equal(t, []subscription{{ID: "sbx", Known: true}}, subscriptions(t, nextFrame(t, p, "subscribe")))

	// When: the new watch sees a stale server hint and no fresh snapshot.
	send(t, p, `{"type":"error","id":"sbx","code":"NOT_FOUND","was_known":false}`)

	// Then: retained knowledge still makes this terminal, not polling fallback.
	require.Equal(t, Update{NotFound: true, At: f.clock.Now()}, receive(t, ch))
	expectClosed(t, ch)
}
