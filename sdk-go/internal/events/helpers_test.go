// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/stretchr/testify/require"
	"go.uber.org/goleak"
)

func TestMain(m *testing.M) { goleak.VerifyTestMain(m) }

type manualClock struct {
	mu     sync.Mutex
	now    time.Time
	timers map[*manualTimer]time.Time
	armed  chan time.Time
}

type manualTimer struct {
	clock *manualClock
	out   chan time.Time
}

func newClock() *manualClock {
	return &manualClock{
		now: time.Unix(1700000000, 0), timers: make(map[*manualTimer]time.Time), armed: make(chan time.Time, 10000),
	}
}

func (c *manualClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *manualClock) NewTimer(d time.Duration) timer {
	c.mu.Lock()
	defer c.mu.Unlock()
	t := &manualTimer{clock: c, out: make(chan time.Time, 1)}
	at := c.now.Add(d)
	if d <= 0 {
		t.out <- c.now
	} else {
		c.timers[t] = at
	}
	c.armed <- at
	return t
}

func (t *manualTimer) C() <-chan time.Time { return t.out }
func (t *manualTimer) Stop() bool {
	t.clock.mu.Lock()
	defer t.clock.mu.Unlock()
	_, active := t.clock.timers[t]
	delete(t.clock.timers, t)
	return active
}

func (c *manualClock) advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.now = c.now.Add(d)
	for t, at := range c.timers {
		if !c.now.Before(at) {
			delete(c.timers, t)
			t.out <- c.now
		}
	}
}

type clientFrame struct {
	Type string            `json:"type"`
	IDs  []json.RawMessage `json:"ids"`
}

type peer struct {
	conn   *websocket.Conn
	frames chan clientFrame
	done   chan struct{}
}

type fixture struct {
	hub      *Hub[Update]
	clock    *manualClock
	accepted chan *peer
}

func newFixture(t *testing.T, idle time.Duration) *fixture {
	t.Helper()
	accepted := make(chan *peer, 20)
	var handlers sync.WaitGroup
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		handlers.Add(1)
		defer handlers.Done()
		conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{Subprotocols: []string{Subprotocol}})
		if err != nil {
			t.Error(err)
			return
		}
		conn.SetReadLimit(maxClientFrame)
		p := &peer{conn: conn, frames: make(chan clientFrame, 1000), done: make(chan struct{})}
		defer close(p.done)
		defer func() {
			if closeErr := conn.CloseNow(); closeErr != nil && !errors.Is(closeErr, net.ErrClosed) {
				t.Error(closeErr)
			}
		}()
		accepted <- p
		for {
			_, data, readErr := conn.Read(r.Context())
			if readErr != nil {
				return
			}
			var f clientFrame
			if decodeErr := json.Unmarshal(data, &f); decodeErr != nil {
				t.Error(decodeErr)
				return
			}
			select {
			case p.frames <- f:
			case <-r.Context().Done():
				return
			}
		}
	}))
	t.Cleanup(func() {
		server.Close()
		handlers.Wait() // Hijacked connections are not joined by httptest.Server.Close.
	})
	h := New(t.Context(), Config[Update]{
		Connect: func(ctx context.Context, _ string) (Connection, error) {
			//nolint:bodyclose // websocket.Dial owns and closes its HTTP response body.
			conn, _, err := websocket.Dial(ctx, server.URL, &websocket.DialOptions{Subprotocols: []string{Subprotocol}})
			return Connection{Socket: conn}, err
		},
		Observe: func(update Update) Update { return update }, IdleClose: idle,
	})
	c := newClock()
	h.clock, h.random = c, func() float64 { return 0.5 }
	t.Cleanup(h.Close)
	return &fixture{hub: h, clock: c, accepted: accepted}
}

func receive[T any](t *testing.T, ch <-chan T) T {
	t.Helper()
	select {
	case value, ok := <-ch:
		require.True(t, ok, "channel closed before expected value")
		return value
	case <-time.After(5 * time.Second):
		t.Fatal("timed out waiting for observable event")
		var zero T
		return zero
	}
}

func send(t *testing.T, p *peer, payload string) {
	t.Helper()
	require.NoError(t, p.conn.Write(t.Context(), websocket.MessageText, []byte(payload)))
}

func expectClosed[T any](t *testing.T, ch <-chan T) {
	t.Helper()
	select {
	case _, ok := <-ch:
		require.False(t, ok, "expected closed channel")
	case <-time.After(5 * time.Second):
		t.Fatal("channel did not close")
	}
}

func nextFrame(t *testing.T, p *peer, kind string) clientFrame {
	t.Helper()
	for {
		f := receive(t, p.frames)
		if f.Type == kind {
			return f
		}
	}
}

func subscriptions(t *testing.T, f clientFrame) []subscription {
	t.Helper()
	out := make([]subscription, len(f.IDs))
	for i, raw := range f.IDs {
		require.NoError(t, json.Unmarshal(raw, &out[i]))
	}
	return out
}
