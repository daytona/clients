// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import (
	"context"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/stretchr/testify/require"
)

func TestIdleCloseJoinsWorkerWhenPeerNeverReadsCloseFrame(t *testing.T) {
	// Given: the peer upgrades but reads neither data nor close frames.
	accepted, finish, finished := make(chan struct{}), make(chan struct{}), make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer close(finished)
		conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{Subprotocols: []string{Subprotocol}})
		if err != nil {
			t.Error(err)
			return
		}
		close(accepted)
		<-finish
		if closeErr := conn.CloseNow(); closeErr != nil && !errors.Is(closeErr, net.ErrClosed) {
			t.Error(closeErr)
		}
	}))
	t.Cleanup(func() {
		close(finish)
		<-finished
		server.Close()
	})
	hub := New(t.Context(), Config[Update]{
		Connect: func(ctx context.Context, _ string) (Connection, error) {
			//nolint:bodyclose // websocket.Dial owns and closes its HTTP response body.
			conn, _, err := websocket.Dial(ctx, server.URL, &websocket.DialOptions{Subprotocols: []string{Subprotocol}})
			return Connection{Socket: conn}, err
		},
		Observe: func(update Update) Update { return update }, IdleClose: time.Second,
	})
	clock := newClock()
	hub.clock = clock
	t.Cleanup(hub.Close)
	_, release := hub.Watch(t.Context(), "sbx", "a")
	<-accepted
	// Wait until the socket loop has armed a timer before releasing the watch.
	receive(t, clock.armed)

	// When
	release()
	clock.advance(time.Second)
	joined := make(chan struct{})
	go func() {
		hub.workers.Wait()
		close(joined)
	}()

	// Then: no five-second WebSocket close handshake, no parked region worker.
	select {
	case <-joined:
	case <-time.After(2 * time.Second):
		t.Fatal("idle socket retained a worker or waited for its peer's close handshake")
	}
}

func TestIdleSocketRestartsWhenNewWatchArrivesAfterRetirement(t *testing.T) {
	// Given
	f := newFixture(t, time.Second)
	_, release := f.hub.Watch(t.Context(), "sbx", "a")
	original := receive(t, f.accepted)
	nextFrame(t, original, "subscribe")
	release()
	f.clock.advance(time.Second)
	expectClosed(t, original.done)
	f.hub.workers.Wait()

	// When
	ch, done := f.hub.Watch(t.Context(), "sbx", "a")
	defer done()
	replacement := receive(t, f.accepted)
	nextFrame(t, replacement, "subscribe")
	send(t, replacement, `{"type":"snapshot","id":"sbx","status":"running","version":1}`)

	// Then
	require.Equal(t, "running", receive(t, ch).Status)
}

func TestPermanentFallbackRetainsOnlyEndpointMetadata(t *testing.T) {
	// Given
	f := newFixture(t, 0)
	f.hub.config.Connect = func(context.Context, string) (Connection, error) {
		return Connection{Permanent: true}, errors.New("endpoint unavailable")
	}

	// When
	ch, release := f.hub.Watch(t.Context(), "sbx", "a")
	defer release()
	expectClosed(t, ch)
	f.hub.workers.Wait()

	// Then: cache the disable decision, not every departed sandbox/listener.
	f.hub.mu.Lock()
	defer f.hub.mu.Unlock()
	require.Len(t, f.hub.regions, 1)
	for _, session := range f.hub.regions {
		require.True(t, session.permanent)
		require.Empty(t, session.entries)
	}
}
