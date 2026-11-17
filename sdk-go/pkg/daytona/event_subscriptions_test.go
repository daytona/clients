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
	"github.com/daytona/clients/sdk-go/pkg/errors"
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

	// handshakeGate, when set, holds socket handshakes until it is closed.
	handshakeGate chan struct{}
	// rejectSockets fails every socket connection attempt.
	rejectSockets bool
	// operation, if set, answers every request other than a GET.
	operation http.HandlerFunc

	writeMu sync.Mutex
	conn    *websocket.Conn

	mu             sync.Mutex
	state          apiclient.SandboxState
	socketAttempts int
	connected      bool
	connectedAt    time.Time
	getTimes       []time.Time
	received       []string
}

func newEventTestServer(t *testing.T, state apiclient.SandboxState) *eventTestServer {
	t.Helper()
	s := &eventTestServer{t: t, state: state}
	upgrader := websocket.Upgrader{CheckOrigin: func(*http.Request) bool { return true }}
	s.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/socket.io") {
			s.mu.Lock()
			s.socketAttempts++
			reject := s.rejectSockets
			gate := s.handshakeGate
			s.mu.Unlock()
			if reject {
				http.Error(w, "unavailable", http.StatusServiceUnavailable)
				return
			}
			if gate != nil {
				select {
				case <-gate:
				case <-r.Context().Done():
					return
				}
			}
			s.serveSocket(upgrader, w, r)
			return
		}

		s.mu.Lock()
		state := s.state
		operation := s.operation
		if r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/sandbox/") {
			s.getTimes = append(s.getTimes, time.Now())
		}
		s.mu.Unlock()

		if operation != nil && r.Method != http.MethodGet {
			operation(w, r)
			return
		}

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
	s.writeMu.Lock()
	s.conn = conn
	s.writeMu.Unlock()

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

func (s *eventTestServer) setOperation(operation http.HandlerFunc) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.operation = operation
}

// emitState sends a sandbox.state.updated event for the test sandbox over the
// current socket connection. Safe to call from handler goroutines.
func (s *eventTestServer) emitState(oldState, newState apiclient.SandboxState) {
	s.t.Helper()
	payload, err := json.Marshal(map[string]any{
		"sandbox":  map[string]any{"id": eventTestSandboxID, "state": newState},
		"oldState": oldState,
		"newState": newState,
	})
	if !assert.NoError(s.t, err) {
		return
	}
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	if !assert.NotNil(s.t, s.conn, "no socket connection") {
		return
	}
	assert.NoError(s.t, s.conn.WriteMessage(websocket.TextMessage, []byte(`42["sandbox.state.updated",`+string(payload)+`]`)))
}

// holdHandshakes makes socket handshakes wait until the returned release is
// called.
func (s *eventTestServer) holdHandshakes() (release func()) {
	gate := make(chan struct{})
	s.mu.Lock()
	s.handshakeGate = gate
	s.mu.Unlock()
	var once sync.Once
	release = func() { once.Do(func() { close(gate) }) }
	s.t.Cleanup(release)
	return release
}

// dropSocket closes the current socket connection from the server side.
func (s *eventTestServer) dropSocket() {
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	if s.conn != nil {
		_ = s.conn.Close()
		s.conn = nil
	}
}

func (s *eventTestServer) getTimesCopy() []time.Time {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]time.Time(nil), s.getTimes...)
}

func (s *eventTestServer) gets() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.getTimes)
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
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTED)
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
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTED)
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
			srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTING)
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
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTING)
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

// minPollGap is the smallest gap accepted between polls scheduled 1 s apart;
// the fast cadence polls every 100 ms.
const minPollGap = 500 * time.Millisecond

func TestWaitForStatePollsAtSubscribedCadenceUntilEventConnectionIsUp(t *testing.T) {
	stubWaitForStatePollRandom(t, 0.5)
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTING)
	release := srv.holdHandshakes()
	client := newEventTestClient(t, srv)
	sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTING)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- sandbox.doWaitForStart(ctx, 0) }()

	// Subscribed while the handshake is held: the immediate poll, then 1 s.
	require.Eventually(t, func() bool { return srv.gets() >= 2 }, 10*time.Second, 10*time.Millisecond)
	times := srv.getTimesCopy()
	assert.GreaterOrEqual(t, times[1].Sub(times[0]), minPollGap, "subscribed waits poll every second, not at the fast cadence")

	release()
	require.Eventually(t, client.subscriptionManager.IsConnected, 10*time.Second, 10*time.Millisecond)
	// At most the poll already scheduled at the subscribed cadence; the next
	// one is 5 s out.
	connectedGets := srv.gets()
	time.Sleep(3500 * time.Millisecond)
	assert.LessOrEqual(t, srv.gets(), connectedGets+1)

	cancel()
	require.ErrorIs(t, <-done, context.Canceled)
}

func TestEventBackedWaitPollsOnlyAsSafetyNet(t *testing.T) {
	// The low jitter bound puts the second poll at 4 s, the earliest it may run.
	stubWaitForStatePollRandom(t, 0)
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTING)
	client := newEventTestClient(t, srv)
	sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTING)
	connectEvents(t, client)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- sandbox.doWaitForStart(ctx, 0) }()

	require.Eventually(t, func() bool { return srv.gets() >= 2 }, 10*time.Second, 10*time.Millisecond)
	times := srv.getTimesCopy()
	assert.GreaterOrEqual(t, times[1].Sub(times[0]), 4*time.Second-minPollGap, "no poll between the immediate one and 4 s")

	cancel()
	require.ErrorIs(t, <-done, context.Canceled)
}

func TestSubscribedWaitPollsAboutEverySecondWhileDisconnected(t *testing.T) {
	stubWaitForStatePollRandom(t, 0.5)
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTING)
	srv.mu.Lock()
	srv.rejectSockets = true
	srv.mu.Unlock()
	client := newEventTestClient(t, srv)
	sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTING)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- sandbox.doWaitForStart(ctx, 0) }()

	require.Eventually(t, func() bool { return srv.gets() >= 3 }, 10*time.Second, 10*time.Millisecond)
	require.Positive(t, srv.socketAttemptCount())
	assert.False(t, client.subscriptionManager.IsConnected())
	times := srv.getTimesCopy()
	for i := 1; i < 3; i++ {
		assert.GreaterOrEqual(t, times[i].Sub(times[i-1]), minPollGap, "poll %d came at the fast cadence", i)
	}

	cancel()
	require.ErrorIs(t, <-done, context.Canceled)
}

func TestSandboxHandleEventAppliesStateFromSlimPayload(t *testing.T) {
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTING)
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
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTING)
	client := newEventTestClient(t, srv)
	sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTING)

	sub := sandbox.subscribeForWait()
	firstID := sub.subID
	require.NotEmpty(t, firstID)
	require.Eventually(t, func() bool { return sub.pollMode() == waitPollEventBacked }, 5*time.Second, 10*time.Millisecond)
	assert.Equal(t, firstID, sub.subID, "a live subscription is refreshed, not replaced")

	// Simulate the manager's TTL expiring the subscription mid-wait.
	client.subscriptionManager.Unsubscribe(firstID)
	assert.False(t, client.subscriptionManager.Refresh(firstID))

	require.Equal(t, waitPollEventBacked, sub.pollMode())
	assert.NotEmpty(t, sub.subID)
	assert.NotEqual(t, firstID, sub.subID, "an expired subscription is replaced by a new one")

	replacementID := sub.subID
	sub.close()
	assert.Empty(t, sub.subID)
	assert.False(t, client.subscriptionManager.Refresh(replacementID), "close releases the replacement subscription")
	require.Eventually(t, func() bool {
		return assert.ObjectsAreEqual([]string{subscribeEvent, unsubscribeEvent, subscribeEvent, unsubscribeEvent}, srv.events())
	}, 5*time.Second, 10*time.Millisecond)
}

// stubNoScheduledPolls leaves a wait only its immediate first read (and the
// final refresh at its deadline), so GET counts show whether that read ran.
func stubNoScheduledPolls(t *testing.T) {
	t.Helper()
	original := waitForStateNextPoll
	waitForStateNextPoll = func(time.Duration, waitPollMode) time.Duration { return time.Hour }
	t.Cleanup(func() { waitForStateNextPoll = original })
}

// readsFirstTimeout bounds waits that must end on their immediate read; a wait
// that skipped it ends only at this deadline, through its final refresh.
const readsFirstTimeout = 5 * time.Second

// assertEndedOnImmediateRead checks that a wait that started at start ended
// well before readsFirstTimeout, with exactly one GET.
func assertEndedOnImmediateRead(t *testing.T, srv *eventTestServer, start time.Time) {
	t.Helper()
	assert.Less(t, time.Since(start), readsFirstTimeout/2, "the wait ended at its deadline, not on its immediate read")
	assert.Equal(t, 1, srv.gets())
}

// connectEvents brings the event connection up through a subscription to
// another sandbox, so the test sandbox's own subscription is its only listener.
func connectEvents(t *testing.T, client *Client) {
	t.Helper()
	holder := client.subscriptionManager.Subscribe("00000000-0000-4000-8000-0000000000ff", func(common.SandboxEvent) {}, []string{"sandbox.state.updated"})
	require.NotEmpty(t, holder)
	t.Cleanup(func() { client.subscriptionManager.Unsubscribe(holder) })
	require.Eventually(t, client.subscriptionManager.IsConnected, 5*time.Second, 10*time.Millisecond)
}

// respondThenEmit answers the operation with the sandbox in respondState and
// sends the event to targetState once the wait is running.
func respondThenEmit(t *testing.T, srv *eventTestServer, respondState, targetState apiclient.SandboxState) http.HandlerFunc {
	return func(w http.ResponseWriter, _ *http.Request) {
		srv.setState(targetState)
		writeJSONResponse(t, w, http.StatusOK, testSandboxPayload(eventTestSandboxID, "sandbox", respondState))
		timer := time.AfterFunc(300*time.Millisecond, func() { srv.emitState(respondState, targetState) })
		t.Cleanup(func() { timer.Stop() })
	}
}

func TestOperationWaitsSkipFirstReadWhenSubscribedBeforeRequest(t *testing.T) {
	tests := []struct {
		name         string
		respondState apiclient.SandboxState
		targetState  apiclient.SandboxState
		run          func(*Sandbox) error
	}{
		{"stop", apiclient.SANDBOXSTATE_STOPPING, apiclient.SANDBOXSTATE_STOPPED, func(s *Sandbox) error {
			return s.doStopWithTimeout(context.Background(), 10*time.Second, false)
		}},
		{"delete and wait", apiclient.SANDBOXSTATE_DESTROYING, apiclient.SANDBOXSTATE_DESTROYED, func(s *Sandbox) error {
			return s.doDeleteAndWait(context.Background(), 10*time.Second)
		}},
		{"pause", apiclient.SANDBOXSTATE_PAUSING, apiclient.SANDBOXSTATE_PAUSED, func(s *Sandbox) error {
			return s.doPauseWithTimeout(context.Background(), 10*time.Second)
		}},
		{"resize", apiclient.SANDBOXSTATE_RESIZING, apiclient.SANDBOXSTATE_STARTED, func(s *Sandbox) error {
			return s.doResizeWithTimeout(context.Background(), &types.Resources{CPU: 4}, 10*time.Second)
		}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			stubNoScheduledPolls(t)
			srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTED)
			client := newEventTestClient(t, srv)
			connectEvents(t, client)
			sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTED)
			srv.setOperation(respondThenEmit(t, srv, tt.respondState, tt.targetState))

			require.NoError(t, tt.run(sandbox))
			assert.Equal(t, tt.targetState, sandbox.currentState())
			assert.Equal(t, 0, srv.gets(), "the response and the state event replace every read")
			require.Eventually(t, func() bool { return srv.hasEvent(unsubscribeEvent) }, 5*time.Second, 10*time.Millisecond)
		})
	}
}

func TestStopReadsFirstWhenEventConnectionIsDown(t *testing.T) {
	stubNoScheduledPolls(t)
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STOPPED)
	srv.mu.Lock()
	srv.rejectSockets = true
	srv.mu.Unlock()
	client := newEventTestClient(t, srv)
	sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTED)
	srv.setOperation(func(w http.ResponseWriter, _ *http.Request) {
		writeJSONResponse(t, w, http.StatusOK, testSandboxPayload(eventTestSandboxID, "sandbox", apiclient.SANDBOXSTATE_STOPPING))
	})

	start := time.Now()
	require.NoError(t, sandbox.doStopWithTimeout(context.Background(), readsFirstTimeout, false))
	assert.Equal(t, apiclient.SANDBOXSTATE_STOPPED, sandbox.currentState())
	assertEndedOnImmediateRead(t, srv, start)
}

func TestStopReadsFirstWhenStateEventArrivesDuringRequest(t *testing.T) {
	stubNoScheduledPolls(t)
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STOPPED)
	client := newEventTestClient(t, srv)
	connectEvents(t, client)
	sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTED)
	srv.setOperation(func(w http.ResponseWriter, _ *http.Request) {
		// The sandbox stops before the (older) response is written.
		srv.emitState(apiclient.SANDBOXSTATE_STOPPING, apiclient.SANDBOXSTATE_STOPPED)
		assert.Eventually(t, func() bool { return sandbox.stateEvents.Load() == 1 }, 5*time.Second, 5*time.Millisecond)
		writeJSONResponse(t, w, http.StatusOK, testSandboxPayload(eventTestSandboxID, "sandbox", apiclient.SANDBOXSTATE_STOPPING))
	})

	start := time.Now()
	require.NoError(t, sandbox.doStopWithTimeout(context.Background(), readsFirstTimeout, false))
	assert.Equal(t, apiclient.SANDBOXSTATE_STOPPED, sandbox.currentState())
	// The event may be newer than the response, so the wait reads at once.
	assertEndedOnImmediateRead(t, srv, start)
}

func TestStopRequestFailureReleasesSubscription(t *testing.T) {
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTED)
	client := newEventTestClient(t, srv)
	connectEvents(t, client)
	sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTED)
	srv.setOperation(func(w http.ResponseWriter, _ *http.Request) {
		writeJSONResponse(t, w, http.StatusInternalServerError, map[string]any{"message": "boom", "statusCode": 500})
	})

	err := sandbox.doStopWithTimeout(context.Background(), 10*time.Second, false)
	require.ErrorIs(t, err, errors.ErrInternalServer)
	assert.Contains(t, err.Error(), "boom")
	require.Eventually(t, func() bool { return srv.hasEvent(unsubscribeEvent) }, 5*time.Second, 10*time.Millisecond)
	assert.True(t, srv.hasEvent(subscribeEvent), "the subscription is taken before the request")
	assert.Equal(t, 0, srv.gets())
}

func TestStopResponseInTargetStateReleasesSubscription(t *testing.T) {
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STOPPED)
	client := newEventTestClient(t, srv)
	connectEvents(t, client)
	sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTED)
	srv.setOperation(func(w http.ResponseWriter, _ *http.Request) {
		writeJSONResponse(t, w, http.StatusOK, testSandboxPayload(eventTestSandboxID, "sandbox", apiclient.SANDBOXSTATE_STOPPED))
	})

	require.NoError(t, sandbox.doStopWithTimeout(context.Background(), 10*time.Second, false))
	assert.Equal(t, 0, srv.gets())
	require.Eventually(t, func() bool { return srv.hasEvent(unsubscribeEvent) }, 5*time.Second, 10*time.Millisecond)
	assert.True(t, srv.hasEvent(subscribeEvent), "the subscription is taken before the request")
}

func TestDeleteWithoutWaitTakesNoSubscription(t *testing.T) {
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_DESTROYING)
	client := newEventTestClient(t, srv)
	sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTED)

	require.NoError(t, sandbox.doDeleteWithTimeout(context.Background(), 10*time.Second))
	time.Sleep(200 * time.Millisecond)
	assert.Equal(t, 0, srv.socketAttemptCount())
	assert.Empty(t, srv.events())
	assert.Equal(t, 0, srv.gets())
}

func TestStopReadsFirstWhenResponseIsErrorState(t *testing.T) {
	stubNoScheduledPolls(t)
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_ERROR)
	client := newEventTestClient(t, srv)
	connectEvents(t, client)
	sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTED)
	srv.setOperation(func(w http.ResponseWriter, _ *http.Request) {
		writeJSONResponse(t, w, http.StatusOK, testSandboxPayload(eventTestSandboxID, "sandbox", apiclient.SANDBOXSTATE_ERROR))
	})

	start := time.Now()
	err := sandbox.doStopWithTimeout(context.Background(), readsFirstTimeout, false)
	require.Error(t, err)
	assert.Contains(t, err.Error(), "error state")
	assertEndedOnImmediateRead(t, srv, start)
}

func newForkSourceSandbox(client *Client) *Sandbox {
	state := apiclient.SANDBOXSTATE_STARTED
	return NewSandbox(client, nil, &apiclient.Sandbox{Id: "00000000-0000-4000-8000-0000000000aa", Name: "source", State: &state}, types.CodeLanguagePython, client.subscriptionManager)
}

func TestForkSkipsFirstReadWhenLive(t *testing.T) {
	stubNoScheduledPolls(t)
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTED)
	client := newEventTestClient(t, srv)
	connectEvents(t, client)
	srv.setOperation(respondThenEmit(t, srv, apiclient.SANDBOXSTATE_STARTING, apiclient.SANDBOXSTATE_STARTED))

	forked, err := newForkSourceSandbox(client).doForkWithTimeout(context.Background(), nil, 10*time.Second)
	require.NoError(t, err)
	assert.Equal(t, eventTestSandboxID, forked.ID)
	assert.Equal(t, apiclient.SANDBOXSTATE_STARTED, forked.currentState())
	assert.Equal(t, 0, srv.gets())
	require.Eventually(t, func() bool { return srv.hasEvent(unsubscribeEvent) }, 5*time.Second, 10*time.Millisecond)
}

func TestForkReadsFirstWhenNotLive(t *testing.T) {
	stubNoScheduledPolls(t)
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTED)
	srv.mu.Lock()
	srv.rejectSockets = true
	srv.mu.Unlock()
	client := newEventTestClient(t, srv)
	srv.setOperation(func(w http.ResponseWriter, _ *http.Request) {
		writeJSONResponse(t, w, http.StatusOK, testSandboxPayload(eventTestSandboxID, "sandbox", apiclient.SANDBOXSTATE_STARTING))
	})

	start := time.Now()
	forked, err := newForkSourceSandbox(client).doForkWithTimeout(context.Background(), nil, readsFirstTimeout)
	require.NoError(t, err)
	assert.Equal(t, apiclient.SANDBOXSTATE_STARTED, forked.currentState())
	assertEndedOnImmediateRead(t, srv, start)
}

func TestForkWithStartedResponseTakesNoSubscription(t *testing.T) {
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTED)
	client := newEventTestClient(t, srv)
	srv.setOperation(func(w http.ResponseWriter, _ *http.Request) {
		writeJSONResponse(t, w, http.StatusOK, testSandboxPayload(eventTestSandboxID, "sandbox", apiclient.SANDBOXSTATE_STARTED))
	})

	forked, err := newForkSourceSandbox(client).doForkWithTimeout(context.Background(), nil, 10*time.Second)
	require.NoError(t, err)
	assert.Equal(t, apiclient.SANDBOXSTATE_STARTED, forked.currentState())
	time.Sleep(200 * time.Millisecond)
	assert.Equal(t, 0, srv.gets())
	assert.Equal(t, 0, srv.socketAttemptCount())
	assert.Empty(t, srv.events())
}

func TestStopReadsFirstWhenEventConnectionReconnectsDuringRequest(t *testing.T) {
	stubNoScheduledPolls(t)
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STARTED)
	client := newEventTestClient(t, srv)
	connectEvents(t, client)
	sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTED)
	srv.setOperation(func(w http.ResponseWriter, _ *http.Request) {
		epoch := client.subscriptionManager.ConnectionEpoch()
		srv.dropSocket()
		// The sandbox stops while the socket is down: its event is lost.
		srv.setState(apiclient.SANDBOXSTATE_STOPPED)
		assert.Eventually(t, func() bool {
			return client.subscriptionManager.ConnectionEpoch() > epoch && client.subscriptionManager.IsConnected()
		}, 10*time.Second, 10*time.Millisecond)
		writeJSONResponse(t, w, http.StatusOK, testSandboxPayload(eventTestSandboxID, "sandbox", apiclient.SANDBOXSTATE_STOPPING))
	})

	start := time.Now()
	require.NoError(t, sandbox.doStopWithTimeout(context.Background(), 15*time.Second, false))
	assert.Equal(t, apiclient.SANDBOXSTATE_STOPPED, sandbox.currentState())
	assert.Equal(t, 1, srv.gets(), "the reconnect may have lost events, so the wait reads at once")
	// The reconnect takes about 1 s; a skipped read would end only at the deadline.
	assert.Less(t, time.Since(start), 10*time.Second)
}

func TestStopReadsFirstWhenLiveResponseIsEmpty(t *testing.T) {
	stubNoScheduledPolls(t)
	srv := newEventTestServer(t, apiclient.SANDBOXSTATE_STOPPED)
	client := newEventTestClient(t, srv)
	connectEvents(t, client)
	sandbox := newEventTestSandbox(client, apiclient.SANDBOXSTATE_STARTED)
	srv.setOperation(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })

	start := time.Now()
	require.NoError(t, sandbox.doStopWithTimeout(context.Background(), readsFirstTimeout, false))
	assert.Equal(t, apiclient.SANDBOXSTATE_STOPPED, sandbox.currentState())
	assertEndedOnImmediateRead(t, srv, start)
}
