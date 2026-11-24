// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import (
	"context"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/stretchr/testify/require"
)

func TestClientPingRearmsOnlyWhenPeerSendsFrame(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	_, release := f.hub.Watch(t.Context(), "sbx", "a")
	defer release()
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")
	f.clock.advance(pingAfter)
	nextFrame(t, p, "ping")

	// When
	f.clock.advance(pingAfter)
	send(t, p, `{"type":"ping"}`)

	// Then: pong is a processing barrier, and a second client ping must not precede it.
	for {
		frame := receive(t, p.frames)
		require.NotEqual(t, "ping", frame.Type)
		if frame.Type == "pong" {
			break
		}
	}
	f.clock.advance(pingAfter)
	nextFrame(t, p, "ping")
}

func TestLivenessReconnectsWhenOpenSocketSendsNothing(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	_, release := f.hub.Watch(t.Context(), "sbx", "a")
	defer release()
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")

	// When
	f.clock.advance(liveness)

	// Then
	expectClosed(t, p.done)
	awaitTimer(t, f.clock, f.clock.Now().Add(125*time.Millisecond))
	f.clock.advance(125 * time.Millisecond)
	reconnected := receive(t, f.accepted)
	nextFrame(t, reconnected, "subscribe")
}

func TestCredentialRefusalFallsBackWhenThreeFreshTicketsFail(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	ch, release := f.hub.Watch(t.Context(), "sbx", "a")
	defer release()

	// When: each 4401 must reconnect through Connect (which mints a fresh ticket).
	for i := range 3 {
		p := receive(t, f.accepted)
		nextFrame(t, p, "subscribe")
		require.NoError(t, p.conn.Close(4401, "credential refused"))
		if i < 2 {
			delay := 125 * time.Millisecond * time.Duration(1<<i)
			awaitTimer(t, f.clock, f.clock.Now().Add(delay))
			f.clock.advance(delay)
		}
	}

	// Then
	expectClosed(t, ch)
}

func TestOversizedServerFrameReconnectsWhenReadLimitExceeded(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	_, release := f.hub.Watch(t.Context(), "sbx", "a")
	defer release()
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")
	// A large frame may be cut off by the peer as soon as its bound is exceeded.
	writeDone := make(chan struct{})

	// When
	go func() {
		defer close(writeDone)
		if err := p.conn.Write(t.Context(), websocket.MessageText, make([]byte, maxServerFrame+1)); err != nil {
			// A read-limit rejection is allowed to interrupt the writer.
			return
		}
	}()

	// Then
	expectClosed(t, p.done)
	expectClosed(t, writeDone)
	awaitTimer(t, f.clock, f.clock.Now().Add(125*time.Millisecond))
	f.clock.advance(125 * time.Millisecond)
	reconnected := receive(t, f.accepted)
	nextFrame(t, reconnected, "subscribe")
}

func TestParentCancellationStopsHubWhenCallerForgetsClose(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	ctx, cancel := context.WithCancel(f.hub.ctx)
	defer cancel()
	f.hub.ctx = ctx
	ch, release := f.hub.Watch(t.Context(), "sbx", "a")
	defer release()
	p := receive(t, f.accepted)
	nextFrame(t, p, "subscribe")

	// When
	cancel()

	// Then
	expectClosed(t, ch)
	expectClosed(t, p.done)
	f.hub.workers.Wait()
}
