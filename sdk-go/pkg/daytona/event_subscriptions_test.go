// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	apiclient "github.com/daytona/clients/api-client-go"
	"github.com/daytona/clients/sdk-go/pkg/common"
	"github.com/daytona/clients/sdk-go/pkg/types"
	"github.com/gorilla/websocket"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

const eventTestSandboxID = "00000000-0000-4000-8000-000000000001"

// eventTestServer serves the sandbox REST endpoints and a minimal Socket.IO v4
// endpoint, recording socket connections and the client events received.
type eventTestServer struct {
	t      *testing.T
	server *httptest.Server

	// handshakeDelay delays accepting socket connections.
	handshakeDelay time.Duration

	mu             sync.Mutex
	state          apiclient.SandboxState
	socketAttempts int
	connected      bool
	connectedAt    time.Time
	getTimes       []time.Time
	received       []string
}

func newEventTestServer(t *testing.T, state apiclient.SandboxState, handshakeDelay time.Duration) *eventTestServer {
	t.Helper()
	s := &eventTestServer{t: t, state: state, handshakeDelay: handshakeDelay}
	upgrader := websocket.Upgrader{CheckOrigin: func(*http.Request) bool { return true }}
	s.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/socket.io") {
			s.mu.Lock()
			s.socketAttempts++
			s.mu.Unlock()
			time.Sleep(s.handshakeDelay)
			s.serveSocket(upgrader, w, r)
			return
		}

		s.mu.Lock()
		state := s.state
		if r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/sandbox/") {
			s.getTimes = append(s.getTimes, time.Now())
		}
		s.mu.Unlock()

		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/sandbox":
			writeJSONResponse(t, w, http.StatusOK, map[string]any{
				"items":      []any{testSandboxPayload(eventTestSandboxID, "sandbox", state)},
				"nextCursor": nil,
			})
		default:
			writeJSONResponse(t, w, http.StatusOK, testSandboxPayload(eventTestSandboxID, "sandbox", state))
		}
	}))
	t.Cleanup(s.server.Close)
	return s
}

func (s *eventTestServer) serveSocket(upgrader websocket.Upgrader, w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer conn.Close()

	if err := conn.WriteMessage(websocket.TextMessage, []byte(`0{"sid":"eio","upgrades":[],"pingInterval":25000,"pingTimeout":20000}`)); err != nil {
		return
	}
	if _, msg, err := conn.ReadMessage(); err != nil || !strings.HasPrefix(string(msg), "40") {
		return
	}
	if err := conn.WriteMessage(websocket.TextMessage, []byte(`40{"sid":"sio"}`)); err != nil {
		return
	}
	s.mu.Lock()
	s.connected = true
	s.connectedAt = time.Now()
	s.mu.Unlock()

	for {
		_, msg, err := conn.ReadMessage()
		if err != nil {
			return
		}
		if !strings.HasPrefix(string(msg), "42") {
			continue
		}
		var packet []json.RawMessage
		if err := json.Unmarshal(msg[2:], &packet); err != nil || len(packet) != 2 {
			continue
		}
		var name string
		var payload struct {
			SandboxIDs []string `json:"sandboxIds"`
		}
		_ = json.Unmarshal(packet[0], &name)
		_ = json.Unmarshal(packet[1], &payload)
		s.mu.Lock()
		s.received = append(s.received, name+":"+strings.Join(payload.SandboxIDs, ","))
		s.mu.Unlock()
	}
}

func (s *eventTestServer) setState(state apiclient.SandboxState) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.state = state
}

func (s *eventTestServer) socketAttemptCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.socketAttempts
}

func (s *eventTestServer) events() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]string(nil), s.received...)
}

func (s *eventTestServer) hasEvent(event string) bool {
	for _, e := range s.events() {
		if e == event {
			return true
		}
	}
	return false
}

// getsBetween counts sandbox GETs in [from, to).
func (s *eventTestServer) getsBetween(from, to time.Time) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	n := 0
	for _, at := range s.getTimes {
		if !at.Before(from) && at.Before(to) {
			n++
		}
	}
	return n
}

// newEventTestClient returns a client wired to srv with an event dispatcher,
// the way NewClient sets it up.
func newEventTestClient(t *testing.T, srv *eventTestServer) *Client {
	t.Helper()
	client := createTestClientWithServer(t, srv.server)
	client.eventDispatcher = common.NewEventDispatcher(srv.server.URL, "test-api-key", "", sdkSource, Version)
	client.subscriptionManager = common.NewEventSubscriptionManager(client.eventDispatcher)
	t.Cleanup(func() { _ = client.Close(context.Background()) })
	return client
}

func newEventTestSandbox(client *Client, state apiclient.SandboxState) *Sandbox {
	return NewSandbox(client, nil, &apiclient.Sandbox{Id: eventTestSandboxID, Name: "sandbox", State: &state}, types.CodeLanguagePython, client.subscriptionManager)
}

var (
	subscribeEvent   = "subscribe:" + eventTestSandboxID
	unsubscribeEvent = "unsubscribe:" + eventTestSandboxID
)

func TestNewClientDoesNotOpenEventConnection(t *testing.T) {
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTED, 0)
	t.Setenv("DAYTONA_USE_DEPRECATED_POLLING", "")

	client, err := NewClientWithConfig(&types.DaytonaConfig{APIKey: "test-api-key", APIUrl: srv.server.URL})
	require.NoError(t, err)
	t.Cleanup(func() { _ = client.Close(context.Background()) })
	require.NotNil(t, client.eventDispatcher)

	// Constructors, get, list and ordinary method calls must not subscribe.
	sandbox, err := client.Get(context.Background(), eventTestSandboxID)
	require.NoError(t, err)
	require.NoError(t, sandbox.RefreshData(context.Background()))
	iter := client.List(context.Background(), nil)
	for iter.Next() {
		require.NotNil(t, iter.Value())
	}
	require.NoError(t, iter.Err())
	_ = newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTED)

	time.Sleep(300 * time.Millisecond)
	assert.Equal(t, 0, srv.socketAttemptCount())
	assert.False(t, client.eventDispatcher.IsConnected())
	assert.Empty(t, srv.events())
}

func TestWaitForStateWithCachedTargetStateDoesNotConnect(t *testing.T) {
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTED, 0)
	client := newEventTestClient(t, srv)
	sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTED)

	require.NoError(t, sandbox.doWaitForStart(context.Background(), 5*time.Second))
	time.Sleep(200 * time.Millisecond)
	assert.Equal(t, 0, srv.socketAttemptCount(), "a wait that returns from the cached state must not subscribe")
	assert.Empty(t, srv.events())
}

func TestWaitForStateSubscribesOnlyForTheDurationOfTheWait(t *testing.T) {
	tests := []struct {
		name string
		// finish ends the wait once the server has seen the subscription.
		finish  func(srv *eventTestServer, cancel context.CancelFunc)
		timeout time.Duration
		check   func(t *testing.T, err error)
	}{
		{
			name:    "success",
			finish:  func(srv *eventTestServer, _ context.CancelFunc) { srv.setState(apiclient.SANDBOXSTATE_STARTED) },
			timeout: 10 * time.Second,
			check:   func(t *testing.T, err error) { require.NoError(t, err) },
		},
		{
			name:    "error state",
			finish:  func(srv *eventTestServer, _ context.CancelFunc) { srv.setState(apiclient.SANDBOXSTATE_ERROR) },
			timeout: 10 * time.Second,
			check: func(t *testing.T, err error) {
				require.Error(t, err)
				assert.Contains(t, err.Error(), "error state")
			},
		},
		{
			name:    "timeout",
			finish:  func(*eventTestServer, context.CancelFunc) {},
			timeout: 1500 * time.Millisecond,
			check: func(t *testing.T, err error) {
				require.Error(t, err)
				assert.Contains(t, err.Error(), "did not start within")
			},
		},
		{
			name:    "cancellation",
			finish:  func(_ *eventTestServer, cancel context.CancelFunc) { cancel() },
			timeout: 10 * time.Second,
			check: func(t *testing.T, err error) {
				require.ErrorIs(t, err, context.Canceled)
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTING, 0)
			client := newEventTestClient(t, srv)
			sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTING)

			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			done := make(chan error, 1)
			go func() { done <- sandbox.doWaitForStart(ctx, tt.timeout) }()

			require.Eventually(t, func() bool { return srv.hasEvent(subscribeEvent) }, 5*time.Second, 10*time.Millisecond)
			assert.False(t, srv.hasEvent(unsubscribeEvent))
			tt.finish(srv, cancel)

			select {
			case err := <-done:
				tt.check(t, err)
			case <-time.After(10 * time.Second):
				t.Fatal("wait did not return")
			}
			require.Eventually(t, func() bool { return srv.hasEvent(unsubscribeEvent) }, 5*time.Second, 10*time.Millisecond)
			assert.Equal(t, []string{subscribeEvent, unsubscribeEvent}, srv.events())
		})
	}
}

func TestConcurrentWaitsShareOneServerSubscription(t *testing.T) {
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTING, 0)
	client := newEventTestClient(t, srv)
	sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTING)

	ctx1, cancel1 := context.WithCancel(context.Background())
	defer cancel1()
	ctx2, cancel2 := context.WithCancel(context.Background())
	defer cancel2()
	done1 := make(chan error, 1)
	done2 := make(chan error, 1)

	go func() { done1 <- sandbox.doWaitForStart(ctx1, 0) }()
	require.Eventually(t, func() bool { return srv.hasEvent(subscribeEvent) }, 5*time.Second, 10*time.Millisecond)
	go func() { done2 <- sandbox.doWaitForStart(ctx2, 0) }()
	time.Sleep(300 * time.Millisecond)

	cancel1()
	require.ErrorIs(t, <-done1, context.Canceled)
	time.Sleep(200 * time.Millisecond)
	assert.Equal(t, []string{subscribeEvent}, srv.events(), "the second wait still needs events")

	cancel2()
	require.ErrorIs(t, <-done2, context.Canceled)
	require.Eventually(t, func() bool { return srv.hasEvent(unsubscribeEvent) }, 5*time.Second, 10*time.Millisecond)
	assert.Equal(t, []string{subscribeEvent, unsubscribeEvent}, srv.events())
}

func TestWaitForStatePollsFastUntilEventConnectionIsUp(t *testing.T) {
	const handshakeDelay = 800 * time.Millisecond
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTING, handshakeDelay)
	client := newEventTestClient(t, srv)
	sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTING)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	start := time.Now()
	go func() { done <- sandbox.doWaitForStart(ctx, 0) }()

	require.Eventually(t, func() bool { return srv.hasEvent(subscribeEvent) }, 5*time.Second, 10*time.Millisecond)
	connectedAt := time.Now()
	time.Sleep(2500 * time.Millisecond)
	cancel()
	require.ErrorIs(t, <-done, context.Canceled)

	// Before the connection is up the wait polls at the fast cadence...
	assert.GreaterOrEqual(t, srv.getsBetween(start, start.Add(600*time.Millisecond)), 3)
	// ...and switches to the sparse safety-net cadence once events can arrive.
	assert.LessOrEqual(t, srv.getsBetween(connectedAt.Add(300*time.Millisecond), connectedAt.Add(2300*time.Millisecond)), 3)
}

func TestSandboxHandleEventAppliesStateFromSlimPayload(t *testing.T) {
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTING, 0)
	client := newEventTestClient(t, srv)
	sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTING)

	sandbox.handleEvent(common.SandboxEvent{
		Type: common.SandboxEventStateUpdated,
		StateEvent: &common.SandboxStateEvent{
			Sandbox:  apiclient.Sandbox{Id: eventTestSandboxID},
			OldState: apiclient.SANDBOXSTATE_STARTING,
			NewState: apiclient.SANDBOXSTATE_STARTED,
		},
	})
	assert.Equal(t, apiclient.SANDBOXSTATE_STARTED, sandbox.currentState())
	assert.Equal(t, "sandbox", sandbox.Name, "a slim event must not clear other fields")

	// Only the state is taken from events; other fields in the payload are ignored.
	sandbox.handleEvent(common.SandboxEvent{
		Type: common.SandboxEventStateUpdated,
		StateEvent: &common.SandboxStateEvent{
			Sandbox:  apiclient.Sandbox{Id: eventTestSandboxID, Name: "renamed", Target: "elsewhere"},
			OldState: apiclient.SANDBOXSTATE_STARTED,
			NewState: apiclient.SANDBOXSTATE_STOPPING,
		},
	})
	assert.Equal(t, apiclient.SANDBOXSTATE_STOPPING, sandbox.currentState())
	sandbox.mu.RLock()
	defer sandbox.mu.RUnlock()
	assert.Equal(t, "sandbox", sandbox.Name)
	assert.Empty(t, sandbox.Target)
}

func TestWaitSubscriptionResubscribesAfterExpiry(t *testing.T) {
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTING, 0)
	client := newEventTestClient(t, srv)
	sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTING)

	sub := sandbox.subscribeForWait()
	firstID := sub.subID
	require.NotEmpty(t, firstID)
	require.Eventually(t, sub.eventBacked, 5*time.Second, 10*time.Millisecond)
	assert.Equal(t, firstID, sub.subID, "a live subscription is refreshed, not replaced")

	// Simulate the manager's TTL expiring the subscription mid-wait.
	client.subscriptionManager.Unsubscribe(firstID)
	assert.False(t, client.subscriptionManager.Refresh(firstID))

	require.True(t, sub.eventBacked())
	assert.NotEmpty(t, sub.subID)
	assert.NotEqual(t, firstID, sub.subID, "an expired subscription is replaced by a new one")

	sub.close()
	assert.False(t, client.subscriptionManager.Refresh(sub.subID))
	require.Eventually(t, func() bool {
		return assert.ObjectsAreEqual([]string{subscribeEvent, unsubscribeEvent, subscribeEvent, unsubscribeEvent}, srv.events())
	}, 5*time.Second, 10*time.Millisecond)
}
