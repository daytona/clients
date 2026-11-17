// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package common

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"runtime"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	apiclient "github.com/daytona/clients/api-client-go"
	"github.com/gorilla/websocket"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// fakeSocketIOServer is a minimal Engine.IO/Socket.IO v4 server that records
// handshake queries and the client events it receives.
type fakeSocketIOServer struct {
	t      *testing.T
	server *httptest.Server

	// holdOpenOnReadError keeps a server socket open after its read side fails
	// until the test ends, instead of closing it.
	holdOpenOnReadError atomic.Bool
	// rejectConnections makes the server refuse new socket handshakes.
	rejectConnections atomic.Bool
	// stopReading makes the server stop reading new sockets after the
	// handshake, so the client's writes back up.
	stopReading atomic.Bool
	done        chan struct{}

	mu       sync.Mutex
	queries  []url.Values
	conns    []*websocket.Conn
	writeMu  sync.Mutex
	received []receivedEvent
}

type receivedEvent struct {
	Name       string
	SandboxIDs []string
}

func newFakeSocketIOServer(t *testing.T) *fakeSocketIOServer {
	t.Helper()
	f := &fakeSocketIOServer{t: t, done: make(chan struct{})}
	upgrader := websocket.Upgrader{CheckOrigin: func(*http.Request) bool { return true }}
	f.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/socket.io") {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		if f.rejectConnections.Load() {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close()

		if err := conn.WriteMessage(websocket.TextMessage, []byte(`0{"sid":"eio","upgrades":[],"pingInterval":25000,"pingTimeout":20000}`)); err != nil {
			return
		}
		_, msg, err := conn.ReadMessage()
		if err != nil || !strings.HasPrefix(string(msg), "40") {
			return
		}
		if err := conn.WriteMessage(websocket.TextMessage, []byte(`40{"sid":"sio"}`)); err != nil {
			return
		}

		f.mu.Lock()
		f.queries = append(f.queries, r.URL.Query())
		f.conns = append(f.conns, conn)
		f.mu.Unlock()

		if f.stopReading.Load() {
			<-f.done
			return
		}
		for {
			_, msg, err := conn.ReadMessage()
			if err != nil {
				if f.holdOpenOnReadError.Load() {
					<-f.done
				}
				return
			}
			if !strings.HasPrefix(string(msg), "42") {
				continue
			}
			var packet []json.RawMessage
			if err := json.Unmarshal(msg[2:], &packet); err != nil || len(packet) != 2 {
				continue
			}
			var event receivedEvent
			_ = json.Unmarshal(packet[0], &event.Name)
			var payload subscriptionMessage
			_ = json.Unmarshal(packet[1], &payload)
			event.SandboxIDs = payload.SandboxIDs
			f.mu.Lock()
			f.received = append(f.received, event)
			f.mu.Unlock()
		}
	}))
	t.Cleanup(f.server.Close)
	t.Cleanup(func() { close(f.done) })
	return f
}

func (f *fakeSocketIOServer) connectionCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.conns)
}

func (f *fakeSocketIOServer) events() []receivedEvent {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]receivedEvent(nil), f.received...)
}

func (f *fakeSocketIOServer) lastQuery() url.Values {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.queries[len(f.queries)-1]
}

// dropConnections closes every server-side socket, as a network drop would.
func (f *fakeSocketIOServer) dropConnections() {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, conn := range f.conns {
		_ = conn.Close()
	}
}

// push sends a server event on the most recent connection.
func (f *fakeSocketIOServer) push(event string, payload string) {
	f.mu.Lock()
	conn := f.conns[len(f.conns)-1]
	f.mu.Unlock()
	f.writeMu.Lock()
	defer f.writeMu.Unlock()
	require.NoError(f.t, conn.WriteMessage(websocket.TextMessage, []byte(fmt.Sprintf(`42[%q,%s]`, event, payload))))
}

func newTestDispatcher(t *testing.T, f *fakeSocketIOServer) *EventDispatcher {
	t.Helper()
	es := NewEventDispatcher(f.server.URL, "test-token", "", "sdk-go", "0.0.0-test")
	t.Cleanup(es.Disconnect)
	return es
}

func testSandboxID(i int) string {
	return fmt.Sprintf("00000000-0000-4000-8000-%012d", i)
}

func noopHandler(SandboxEvent) {}

var stateEvents = []string{"sandbox.state.updated"}

func TestBuildWebSocketURLRequestsExplicitSubscriptions(t *testing.T) {
	wsURL, err := buildWebSocketURL("https://api.example.test/api", "org-1", "sdk-go", "1.2.3")
	require.NoError(t, err)
	parsed, err := url.Parse(wsURL)
	require.NoError(t, err)
	assert.Equal(t, "wss", parsed.Scheme)
	assert.Equal(t, "/api/socket.io/", parsed.Path)
	query := parsed.Query()
	assert.Equal(t, "explicit", query.Get("subscriptionMode"))
	assert.Equal(t, "4", query.Get("EIO"))
	assert.Equal(t, "websocket", query.Get("transport"))
	assert.Equal(t, "org-1", query.Get("organizationId"))
	assert.Equal(t, "sdk-go", query.Get("source"))
	assert.Equal(t, "1.2.3", query.Get("sdkVersion"))
}

func TestEventDispatcherConnectsOnFirstSubscription(t *testing.T) {
	f := newFakeSocketIOServer(t)
	es := newTestDispatcher(t, f)

	time.Sleep(200 * time.Millisecond)
	assert.Equal(t, 0, f.connectionCount(), "constructing the dispatcher must not connect")
	assert.False(t, es.IsConnected())

	id := testSandboxID(1)
	unsubscribe := es.Subscribe(id, noopHandler, stateEvents)
	defer unsubscribe()

	require.Eventually(t, func() bool { return len(f.events()) == 1 }, 5*time.Second, 10*time.Millisecond)
	assert.Equal(t, "explicit", f.lastQuery().Get("subscriptionMode"))
	assert.Equal(t, "sdk-go", f.lastQuery().Get("source"))
	assert.Equal(t, []receivedEvent{{Name: "subscribe", SandboxIDs: []string{id}}}, f.events())

	// The id must be sent once, by the connect handler only.
	time.Sleep(200 * time.Millisecond)
	assert.Len(t, f.events(), 1)
	assert.True(t, es.IsConnected())
}

func TestEventDispatcherSendsSubscribeAndUnsubscribeOnFirstAndLastListener(t *testing.T) {
	f := newFakeSocketIOServer(t)
	es := newTestDispatcher(t, f)

	idA := testSandboxID(1)
	idB := testSandboxID(2)
	unsubscribeA1 := es.Subscribe(idA, noopHandler, stateEvents)
	require.Eventually(t, func() bool { return len(f.events()) == 1 }, 5*time.Second, 10*time.Millisecond)

	unsubscribeA2 := es.Subscribe(idA, noopHandler, stateEvents)
	unsubscribeB := es.Subscribe(idB, noopHandler, stateEvents)
	unsubscribeA1()
	unsubscribeA2()
	unsubscribeA2() // repeated calls send nothing more
	unsubscribeB()

	require.Eventually(t, func() bool { return len(f.events()) == 4 }, 5*time.Second, 10*time.Millisecond)
	assert.Equal(t, []receivedEvent{
		{Name: "subscribe", SandboxIDs: []string{idA}},
		{Name: "subscribe", SandboxIDs: []string{idB}},
		{Name: "unsubscribe", SandboxIDs: []string{idA}},
		{Name: "unsubscribe", SandboxIDs: []string{idB}},
	}, f.events())

	time.Sleep(200 * time.Millisecond)
	assert.Len(t, f.events(), 4)
}

func TestEventDispatcherResendsWholeSetInChunksAfterReconnect(t *testing.T) {
	f := newFakeSocketIOServer(t)
	es := newTestDispatcher(t, f)

	const total = 250
	unsubscribes := make([]func(), 0, total)
	for i := 0; i < total; i++ {
		unsubscribes = append(unsubscribes, es.Subscribe(testSandboxID(i), noopHandler, stateEvents))
	}
	defer func() {
		for _, unsubscribe := range unsubscribes {
			unsubscribe()
		}
	}()

	countIDs := func(events []receivedEvent) int {
		n := 0
		for _, event := range events {
			n += len(event.SandboxIDs)
		}
		return n
	}
	require.Eventually(t, func() bool { return countIDs(f.events()) >= total }, 5*time.Second, 10*time.Millisecond)
	var initialIDs []string
	for _, event := range f.events() {
		assert.Equal(t, "subscribe", event.Name)
		assert.LessOrEqual(t, len(event.SandboxIDs), maxSubscriptionIDsPerMessage)
		initialIDs = append(initialIDs, event.SandboxIDs...)
	}
	subscribed := make([]string, 0, total)
	for i := 0; i < total; i++ {
		subscribed = append(subscribed, testSandboxID(i))
	}
	sort.Strings(initialIDs)
	assert.Equal(t, subscribed, initialIDs, "each subscribed id is sent exactly once")
	before := len(f.events())

	// Keep the client disconnected until the listener changes below are made.
	f.rejectConnections.Store(true)
	f.dropConnections()
	require.Eventually(t, func() bool { return !es.IsConnected() }, 5*time.Second, 10*time.Millisecond)

	// While disconnected, listener changes only update the tracked set.
	unsubscribes[0]()
	added := es.Subscribe(testSandboxID(total), noopHandler, stateEvents)
	defer added()
	time.Sleep(100 * time.Millisecond)
	assert.Len(t, f.events(), before)
	assert.False(t, es.IsConnected())
	f.rejectConnections.Store(false)

	require.Eventually(t, func() bool { return f.connectionCount() == 2 && len(f.events()) == before+3 }, 10*time.Second, 20*time.Millisecond)
	resent := f.events()[before:]
	var ids []string
	for _, event := range resent {
		assert.Equal(t, "subscribe", event.Name)
		ids = append(ids, event.SandboxIDs...)
	}
	assert.Len(t, resent[0].SandboxIDs, 100)
	assert.Len(t, resent[1].SandboxIDs, 100)
	assert.Len(t, resent[2].SandboxIDs, 50)
	expected := make([]string, 0, total)
	for i := 1; i <= total; i++ {
		expected = append(expected, testSandboxID(i))
	}
	assert.ElementsMatch(t, expected, ids, "the resent set must be exactly the current set, each id once")
}

func lowerSubscriptionCap(t *testing.T, limit int) {
	t.Helper()
	original := MaxSubscriptionsPerConnection
	MaxSubscriptionsPerConnection = limit
	t.Cleanup(func() { MaxSubscriptionsPerConnection = original })
}

func TestEventDispatcherServesAtMostTheCap(t *testing.T) {
	lowerSubscriptionCap(t, 2)
	f := newFakeSocketIOServer(t)
	es := newTestDispatcher(t, f)
	manager := NewEventSubscriptionManager(es)
	idA, idB, idC, idD := testSandboxID(1), testSandboxID(2), testSandboxID(3), testSandboxID(4)

	unsubscribeA := es.Subscribe(idA, noopHandler, stateEvents)
	require.Eventually(t, func() bool { return es.IsConnected() && len(f.events()) == 1 }, 5*time.Second, 10*time.Millisecond)
	unsubscribeB := es.Subscribe(idB, noopHandler, stateEvents)
	defer unsubscribeB()
	require.Eventually(t, func() bool { return len(f.events()) == 2 }, 5*time.Second, 10*time.Millisecond)
	unsubscribeC := es.Subscribe(idC, noopHandler, stateEvents)
	time.Sleep(100 * time.Millisecond)
	assert.Equal(t, []receivedEvent{{"subscribe", []string{idA}}, {"subscribe", []string{idB}}}, f.events(), "nothing is sent beyond the cap")
	assert.True(t, manager.IsEventBacked(idA))
	assert.False(t, es.IsServed(idC))
	assert.False(t, manager.IsEventBacked(idC))

	// A freed slot goes to the next new id, not to the waiting one.
	unsubscribeA()
	unsubscribeD := es.Subscribe(idD, noopHandler, stateEvents)
	defer unsubscribeD()
	require.Eventually(t, func() bool { return len(f.events()) == 4 }, 5*time.Second, 10*time.Millisecond)
	assert.Equal(t, receivedEvent{"unsubscribe", []string{idA}}, f.events()[2])
	assert.Equal(t, receivedEvent{"subscribe", []string{idD}}, f.events()[3])
	assert.False(t, es.IsServed(idC))

	// The last listener of an unserved id goes without a message.
	unsubscribeC()
	time.Sleep(100 * time.Millisecond)
	assert.Len(t, f.events(), 4)
}

func TestEventDispatcherResendsOnlyServedIDsAfterReconnect(t *testing.T) {
	lowerSubscriptionCap(t, 2)
	f := newFakeSocketIOServer(t)
	es := newTestDispatcher(t, f)
	idA, idB, idC := testSandboxID(1), testSandboxID(2), testSandboxID(3)
	for _, id := range []string{idA, idB, idC} {
		defer es.Subscribe(id, noopHandler, stateEvents)()
	}
	require.Eventually(t, func() bool { return es.IsConnected() && len(f.events()) >= 1 }, 5*time.Second, 10*time.Millisecond)
	time.Sleep(100 * time.Millisecond)
	before := len(f.events())

	f.dropConnections()
	require.Eventually(t, func() bool { return f.connectionCount() == 2 && len(f.events()) > before }, 10*time.Second, 20*time.Millisecond)
	time.Sleep(100 * time.Millisecond)
	assert.Equal(t, []receivedEvent{{"subscribe", []string{idA, idB}}}, f.events()[before:])
}

func TestEventDispatcherReconnectsAfterFailedSubscriptionWrite(t *testing.T) {
	f := newFakeSocketIOServer(t)
	// Keep server sockets open when their read side ends, so only the client's
	// write failure can end the connection.
	f.holdOpenOnReadError.Store(true)
	es := newTestDispatcher(t, f)

	idA := testSandboxID(1)
	idB := testSandboxID(2)
	unsubscribeA := es.Subscribe(idA, noopHandler, stateEvents)
	defer unsubscribeA()
	require.Eventually(t, func() bool { return len(f.events()) == 1 }, 5*time.Second, 10*time.Millisecond)

	// Break the client's write direction while its read side stays open.
	es.mu.RLock()
	tcpConn, ok := es.client.conn.UnderlyingConn().(*net.TCPConn)
	es.mu.RUnlock()
	require.True(t, ok)
	require.NoError(t, tcpConn.CloseWrite())

	unsubscribeB := es.Subscribe(idB, noopHandler, stateEvents)
	defer unsubscribeB()

	require.Eventually(t, func() bool { return f.connectionCount() == 2 && len(f.events()) == 2 }, 10*time.Second, 20*time.Millisecond)
	resent := f.events()[1]
	assert.Equal(t, "subscribe", resent.Name)
	assert.ElementsMatch(t, []string{idA, idB}, resent.SandboxIDs)
	assert.True(t, es.IsConnected())
}

// countWriteLoops returns the number of running socketIOClient writer goroutines.
func countWriteLoops() int {
	buf := make([]byte, 1<<20)
	for {
		n := runtime.Stack(buf, true)
		if n < len(buf) {
			return strings.Count(string(buf[:n]), "(*socketIOClient).writeLoop(")
		}
		buf = make([]byte, 2*len(buf))
	}
}

func TestSocketIOClientWriterExitsWhenServerDropsConnection(t *testing.T) {
	f := newFakeSocketIOServer(t)
	// Let writers of earlier tests' clients finish.
	require.Eventually(t, func() bool { return countWriteLoops() == 0 }, 5*time.Second, 10*time.Millisecond)

	disconnected := make(chan struct{})
	client, err := newSocketIOClient(socketIOClientConfig{
		APIURL:       f.server.URL,
		Token:        "test-token",
		OnDisconnect: func() { close(disconnected) },
	})
	require.NoError(t, err)
	t.Cleanup(client.Close)
	require.Eventually(t, func() bool { return countWriteLoops() == 1 }, 5*time.Second, 10*time.Millisecond)
	// The server registers the socket just after completing the handshake.
	require.Eventually(t, func() bool { return f.connectionCount() == 1 }, 5*time.Second, 10*time.Millisecond)

	f.dropConnections()
	select {
	case <-disconnected:
	case <-time.After(5 * time.Second):
		t.Fatal("client did not report the disconnect")
	}

	// The writer must not outlive the connection waiting for the owner's Close.
	require.Eventually(t, func() bool { return countWriteLoops() == 0 }, 5*time.Second, 10*time.Millisecond)
	assert.Error(t, client.emit("subscribe", json.RawMessage(`{"sandboxIds":[]}`)))
}

func TestSocketIOClientCloseInterruptsStalledWrite(t *testing.T) {
	f := newFakeSocketIOServer(t)
	f.stopReading.Store(true)
	client, err := newSocketIOClient(socketIOClientConfig{APIURL: f.server.URL, Token: "test-token"})
	require.NoError(t, err)
	t.Cleanup(client.Close)

	// Far more than the socket buffers hold: the writer stalls mid-write.
	payload := json.RawMessage(`"` + strings.Repeat("x", 1<<20) + `"`)
	for i := 0; i < 64; i++ {
		require.NoError(t, client.emit("subscribe", payload))
	}
	writerHoldsLock := func() bool {
		if client.writemu.TryLock() {
			client.writemu.Unlock()
			return false
		}
		return true
	}
	require.Eventually(t, func() bool {
		if !writerHoldsLock() {
			return false
		}
		time.Sleep(200 * time.Millisecond)
		return writerHoldsLock()
	}, 10*time.Second, 10*time.Millisecond, "the writer never stalled")

	closed := make(chan struct{})
	start := time.Now()
	go func() {
		client.Close()
		close(closed)
	}()
	select {
	case <-closed:
	case <-time.After(5 * time.Second):
		t.Fatal("Close blocked on the stalled write")
	}
	assert.Less(t, time.Since(start), time.Second)
}

func TestEventDispatcherCountsConnectionEpochs(t *testing.T) {
	f := newFakeSocketIOServer(t)
	es := newTestDispatcher(t, f)
	assert.Equal(t, uint64(0), es.ConnectionEpoch())

	unsubscribe := es.Subscribe(testSandboxID(1), noopHandler, stateEvents)
	defer unsubscribe()
	require.Eventually(t, es.IsConnected, 5*time.Second, 10*time.Millisecond)
	assert.Equal(t, uint64(1), es.ConnectionEpoch())

	f.dropConnections()
	require.Eventually(t, func() bool { return es.ConnectionEpoch() == 2 && es.IsConnected() }, 10*time.Second, 10*time.Millisecond)
	manager := NewEventSubscriptionManager(es)
	assert.Equal(t, uint64(2), manager.ConnectionEpoch())
	assert.Equal(t, uint64(0), NewEventSubscriptionManager(nil).ConnectionEpoch())
}

func TestEventSubscriptionManagerIsNotConnectedAfterShutdown(t *testing.T) {
	f := newFakeSocketIOServer(t)
	es := newTestDispatcher(t, f)
	manager := NewEventSubscriptionManager(es)

	require.NotEmpty(t, manager.Subscribe(testSandboxID(1), noopHandler, stateEvents))
	require.Eventually(t, manager.IsConnected, 5*time.Second, 10*time.Millisecond)

	manager.Shutdown()
	assert.True(t, es.IsConnected(), "the dispatcher lingers before disconnecting")
	assert.False(t, manager.IsConnected())
}

func TestEventDispatcherDeliversSlimStateEventsAndFiltersByID(t *testing.T) {
	f := newFakeSocketIOServer(t)
	es := newTestDispatcher(t, f)

	id := testSandboxID(1)
	got := make(chan SandboxEvent, 4)
	unsubscribe := es.Subscribe(id, func(event SandboxEvent) { got <- event }, stateEvents)
	defer unsubscribe()
	require.Eventually(t, func() bool { return len(f.events()) == 1 }, 5*time.Second, 10*time.Millisecond)

	// An event for another sandbox (older servers send all organization events) is dropped.
	f.push("sandbox.state.updated", fmt.Sprintf(`{"sandbox":{"id":%q,"state":"started"},"oldState":"starting","newState":"started"}`, testSandboxID(2)))
	f.push("sandbox.state.updated", fmt.Sprintf(`{"sandbox":{"id":%q,"state":"started","desiredState":"started"},"oldState":"starting","newState":"started"}`, id))

	select {
	case event := <-got:
		require.Equal(t, SandboxEventStateUpdated, event.Type)
		require.NotNil(t, event.StateEvent)
		assert.Equal(t, id, event.StateEvent.Sandbox.Id)
		assert.Equal(t, apiclient.SANDBOXSTATE_STARTING, event.StateEvent.OldState)
		assert.Equal(t, apiclient.SANDBOXSTATE_STARTED, event.StateEvent.NewState)
	case <-time.After(5 * time.Second):
		t.Fatal("slim state event was not delivered")
	}
	select {
	case event := <-got:
		t.Fatalf("unexpected event delivered: %+v", event)
	case <-time.After(100 * time.Millisecond):
	}
}

func TestHandleEventParsesSlimAndFullStatePayloads(t *testing.T) {
	es := NewEventDispatcher("http://127.0.0.1:1", "test-token", "", "sdk-go", "0.0.0-test")
	t.Cleanup(es.Disconnect)

	id := testSandboxID(7)
	var got []SandboxEvent
	es.RegisterEvents([]string{"sandbox.state.updated", "sandbox.desired-state.updated"})
	es.mu.Lock()
	es.listeners[id] = map[int]SandboxEventHandler{0: func(event SandboxEvent) { got = append(got, event) }}
	es.mu.Unlock()

	slim := fmt.Sprintf(`{"sandbox":{"id":%q,"state":"error","desiredState":"started","errorReason":"boom","recoverable":true,"updatedAt":"2026-01-01T00:00:00.000Z"},"oldState":"starting","newState":"error"}`, id)
	es.handleEvent("sandbox.state.updated", json.RawMessage(slim))
	require.Len(t, got, 1)
	stateEvent := got[0].StateEvent
	require.NotNil(t, stateEvent)
	assert.Equal(t, id, stateEvent.Sandbox.Id)
	assert.Equal(t, apiclient.SANDBOXSTATE_ERROR, stateEvent.Sandbox.GetState())
	assert.Equal(t, "boom", stateEvent.Sandbox.GetErrorReason())
	assert.True(t, stateEvent.Sandbox.GetRecoverable())
	assert.Equal(t, apiclient.SANDBOXSTATE_STARTING, stateEvent.OldState)
	assert.Equal(t, apiclient.SANDBOXSTATE_ERROR, stateEvent.NewState)

	full, err := json.Marshal(map[string]any{
		"sandbox": map[string]any{
			"id": id, "organizationId": "org", "name": "name", "user": "user", "env": map[string]string{},
			"labels": map[string]string{}, "public": false, "networkBlockAll": false, "kvm": false,
			"target": "us", "cpu": 1, "gpu": 0, "memory": 1, "disk": 1, "toolboxProxyUrl": "http://proxy.test",
			"state": "started",
		},
		"oldState": "starting",
		"newState": "started",
	})
	require.NoError(t, err)
	es.handleEvent("sandbox.state.updated", full)
	require.Len(t, got, 2)
	assert.Equal(t, "org", got[1].StateEvent.Sandbox.OrganizationId)
	assert.Equal(t, apiclient.SANDBOXSTATE_STARTED, got[1].StateEvent.NewState)

	slimDesired := fmt.Sprintf(`{"sandbox":{"id":%q},"oldDesiredState":"started","newDesiredState":"stopped"}`, id)
	es.handleEvent("sandbox.desired-state.updated", json.RawMessage(slimDesired))
	require.Len(t, got, 3)
	require.NotNil(t, got[2].DesiredStateEvent)
	assert.Equal(t, apiclient.SANDBOXDESIREDSTATE_STOPPED, got[2].DesiredStateEvent.NewDesiredState)
}
