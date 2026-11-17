// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package common

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"sync"
	"time"

	apiclient "github.com/daytona/clients/api-client-go"
)

type SandboxStateEvent struct {
	Sandbox  apiclient.Sandbox      `json:"sandbox"`
	OldState apiclient.SandboxState `json:"oldState"`
	NewState apiclient.SandboxState `json:"newState"`
}

type SandboxDesiredStateEvent struct {
	Sandbox         apiclient.Sandbox             `json:"sandbox"`
	OldDesiredState apiclient.SandboxDesiredState `json:"oldDesiredState"`
	NewDesiredState apiclient.SandboxDesiredState `json:"newDesiredState"`
}

type SandboxEventType int

const (
	SandboxEventStateUpdated SandboxEventType = iota
	SandboxEventDesiredStateUpdated
	SandboxEventCreated
)

type SandboxEvent struct {
	Type              SandboxEventType
	StateEvent        *SandboxStateEvent
	DesiredStateEvent *SandboxDesiredStateEvent
	CreatedEvent      *apiclient.Sandbox
}

type SandboxEventHandler func(event SandboxEvent)

// maxSubscriptionIDsPerMessage is the most resource ids sent in a single
// subscribe or unsubscribe message.
const maxSubscriptionIDsPerMessage = 100

// gatewaySubscriptionLimit is the most resource ids the gateway's wire contract
// lets one connection subscribe to; a subscribe beyond it is rejected whole,
// and the client is not told.
const gatewaySubscriptionLimit = 1000

// MaxSubscriptionsPerConnection is the most resource ids one event connection
// subscribes to. It may only be lowered (tests do): each dispatcher reads it
// once, when created, and clamps it to the gateway's limit, so a larger value
// never sends subscribes the gateway would silently reject.
var MaxSubscriptionsPerConnection = gatewaySubscriptionLimit

// subscriptionMessage is the payload of the subscribe and unsubscribe client
// events.
type subscriptionMessage struct {
	SandboxIDs []string `json:"sandboxIds"`
}

// EventDispatcher owns the event socket for a client. It connects lazily when
// the first listener subscribes, tells the server which resource ids currently
// have listeners (so the server only sends events for those), and still filters
// incoming events by id because older servers send every organization event.
type EventDispatcher struct {
	mu sync.RWMutex

	apiURL         string
	token          string
	organizationID string
	source         string
	sdkVersion     string
	client         *socketIOClient
	clientGen      uint64
	connected      bool
	// connectEpoch counts successful connects, reconnects included. Events
	// emitted while disconnected are lost, so a changed epoch means a gap.
	connectEpoch uint64
	closed       bool
	failed       bool
	failError    string

	listeners map[string]map[int]SandboxEventHandler
	nextSubID int
	// served holds the ids the server serves, or will after the next connect:
	// at most maxSubscriptions of the ids with listeners. An id that found no
	// room stays unserved until its last listener goes.
	served           map[string]struct{}
	maxSubscriptions int

	registeredEvents map[string]bool

	disconnectTimer      *time.Timer
	disconnectGeneration uint64

	connecting       bool
	reconnecting     bool
	reconnectAttempt int
	maxReconnects    int
	closeCh          chan struct{}
}

// subscriptionLimit clamps a configured per-connection limit to (0, gateway limit].
func subscriptionLimit(configured int) int {
	if configured <= 0 || configured > gatewaySubscriptionLimit {
		return gatewaySubscriptionLimit
	}
	return configured
}

func NewEventDispatcher(apiURL, token, organizationID, source, sdkVersion string) *EventDispatcher {
	return &EventDispatcher{
		apiURL:           apiURL,
		token:            token,
		organizationID:   organizationID,
		source:           source,
		sdkVersion:       sdkVersion,
		listeners:        make(map[string]map[int]SandboxEventHandler),
		served:           make(map[string]struct{}),
		maxSubscriptions: subscriptionLimit(MaxSubscriptionsPerConnection),
		registeredEvents: make(map[string]bool),
		maxReconnects:    100,
		closeCh:          make(chan struct{}),
	}
}

func (es *EventDispatcher) EnsureConnected() {
	es.mu.Lock()
	if es.closed || es.connected || es.reconnecting || es.connecting {
		es.mu.Unlock()
		return
	}
	es.mu.Unlock()

	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = es.Connect(ctx)
	}()
}

func (es *EventDispatcher) Connect(ctx context.Context) error {
	es.mu.Lock()
	if es.closed || es.connected {
		es.mu.Unlock()
		return nil
	}
	if es.connecting {
		// Another goroutine is already dialing. Report a non-nil result so callers
		// like reconnectLoop keep retrying instead of assuming this in-flight attempt
		// succeeded (it may still fail).
		es.mu.Unlock()
		return fmt.Errorf("connection attempt already in progress")
	}
	es.connecting = true
	select {
	case <-es.closeCh:
		es.closeCh = make(chan struct{})
	default:
	}
	es.clientGen++
	clientGen := es.clientGen
	oldClient := es.client
	es.client = nil
	es.mu.Unlock()

	if oldClient != nil {
		oldClient.Close()
	}

	timeout := 5 * time.Second
	if deadline, ok := ctx.Deadline(); ok {
		timeout = time.Until(deadline)
	}

	client, err := newSocketIOClient(socketIOClientConfig{
		APIURL:         es.apiURL,
		Token:          es.token,
		OrganizationID: es.organizationID,
		Source:         es.source,
		SdkVersion:     es.sdkVersion,
		ConnectTimeout: timeout,
		EventHandler:   es.handleEvent,
		OnDisconnect: func() {
			es.handleDisconnect(clientGen)
		},
	})
	if err != nil {
		es.mu.Lock()
		es.failed = true
		es.failError = fmt.Sprintf("WebSocket connection failed: %v", err)
		es.connecting = false
		errMsg := es.failError
		es.mu.Unlock()
		return fmt.Errorf("%s", errMsg)
	}

	es.mu.Lock()
	if es.closed {
		es.connecting = false
		es.mu.Unlock()
		client.Close()
		return nil
	}
	es.client = client
	es.connected = true
	es.connectEpoch++
	es.connecting = false
	es.failed = false
	es.failError = ""
	es.reconnectAttempt = 0
	// Subscriptions belong to the socket: send the whole current set on every
	// connect and reconnect.
	es.emitSubscriptionLocked("subscribe", es.servedIDsLocked())
	if len(es.listeners) == 0 {
		es.scheduleDelayedDisconnectLocked()
	}
	es.mu.Unlock()

	return nil
}

func (es *EventDispatcher) RegisterEvents(events []string) {
	es.mu.Lock()
	defer es.mu.Unlock()

	for _, event := range events {
		es.registeredEvents[event] = true
	}
}

const disconnectDelay = 30 * time.Second

func (es *EventDispatcher) Subscribe(resourceID string, handler SandboxEventHandler, events []string) func() {
	es.mu.Lock()
	if es.closed {
		es.mu.Unlock()
		// No-op after disconnect
		return func() {}
	}
	es.disconnectGeneration++
	if es.disconnectTimer != nil {
		es.disconnectTimer.Stop()
		es.disconnectTimer = nil
	}

	subID := es.nextSubID
	es.nextSubID++
	if es.listeners[resourceID] == nil {
		es.listeners[resourceID] = make(map[int]SandboxEventHandler)
		// First listener for this id. While disconnected the next connect
		// sends the whole set instead. Beyond the cap nothing is sent: the
		// server would reject it, and the id's waits poll instead.
		if len(es.served) < es.maxSubscriptions {
			es.served[resourceID] = struct{}{}
			es.emitSubscriptionLocked("subscribe", []string{resourceID})
		}
	}
	es.listeners[resourceID][subID] = handler
	// Mark events as registered under lock to prevent drops
	for _, event := range events {
		es.registeredEvents[event] = true
	}
	es.mu.Unlock()

	es.EnsureConnected()

	return func() {
		es.mu.Lock()
		handlers, ok := es.listeners[resourceID]
		if ok {
			if _, exists := handlers[subID]; exists {
				delete(handlers, subID)
				if len(handlers) == 0 {
					delete(es.listeners, resourceID)
					if _, served := es.served[resourceID]; served {
						delete(es.served, resourceID)
						es.emitSubscriptionLocked("unsubscribe", []string{resourceID})
					}
				}
			}
		}

		if len(es.listeners) == 0 {
			es.scheduleDelayedDisconnectLocked()
		}
		es.mu.Unlock()
	}
}

// servedIDsLocked returns the served resource ids, sorted.
func (es *EventDispatcher) servedIDsLocked() []string {
	ids := make([]string, 0, len(es.served))
	for id := range es.served {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}

// IsEventBacked reports, under one lock, whether events for resourceID can
// arrive now: the connection is up and resourceID is served.
func (es *EventDispatcher) IsEventBacked(resourceID string) bool {
	es.mu.RLock()
	defer es.mu.RUnlock()
	_, served := es.served[resourceID]
	return es.connected && served
}

// IsServed reports whether the server is asked to send events for resourceID:
// it has listeners and was within the per-connection cap when it got its first.
func (es *EventDispatcher) IsServed(resourceID string) bool {
	es.mu.RLock()
	defer es.mu.RUnlock()
	_, served := es.served[resourceID]
	return served
}

// emitSubscriptionLocked sends a subscribe or unsubscribe message for ids on the
// current connection, chunked to maxSubscriptionIDsPerMessage ids per message.
// It is fire-and-forget (acknowledgements are not awaited) and does nothing
// while disconnected; nothing is buffered for a later connection because every
// connect re-sends the current set.
func (es *EventDispatcher) emitSubscriptionLocked(event string, ids []string) {
	if !es.connected || es.client == nil {
		return
	}
	for start := 0; start < len(ids); start += maxSubscriptionIDsPerMessage {
		end := min(start+maxSubscriptionIDsPerMessage, len(ids))
		payload, err := json.Marshal(subscriptionMessage{SandboxIDs: ids[start:end]})
		if err != nil {
			return
		}
		_ = es.client.emit(event, payload)
	}
}

func (es *EventDispatcher) IsConnected() bool {
	es.mu.RLock()
	defer es.mu.RUnlock()
	return es.connected
}

// ConnectionEpoch returns the number of successful connects so far.
func (es *EventDispatcher) ConnectionEpoch() uint64 {
	es.mu.RLock()
	defer es.mu.RUnlock()
	return es.connectEpoch
}

func (es *EventDispatcher) IsFailed() bool {
	es.mu.RLock()
	defer es.mu.RUnlock()
	return es.failed
}

func (es *EventDispatcher) FailError() string {
	es.mu.RLock()
	defer es.mu.RUnlock()
	return es.failError
}

func (es *EventDispatcher) Disconnect() {
	es.mu.Lock()
	defer es.mu.Unlock()
	es.disconnectLocked(true)
}

func (es *EventDispatcher) disconnectLocked(permanent bool) {
	select {
	case <-es.closeCh:
	default:
		close(es.closeCh)
	}
	if permanent {
		es.closed = true
	}
	es.connecting = false
	es.reconnecting = false
	if es.disconnectTimer != nil {
		es.disconnectTimer.Stop()
		es.disconnectTimer = nil
	}
	if es.client != nil {
		es.client.Close()
		es.client = nil
	}
	es.connected = false
	es.listeners = make(map[string]map[int]SandboxEventHandler)
	es.served = make(map[string]struct{})
	es.registeredEvents = make(map[string]bool)
}

func (es *EventDispatcher) handleEvent(eventName string, data json.RawMessage) {
	es.mu.RLock()
	registered := es.registeredEvents[eventName]
	es.mu.RUnlock()

	if !registered {
		return
	}

	entityID := extractEntityID(data)
	if entityID == "" {
		return
	}

	var event SandboxEvent
	switch eventName {
	case "sandbox.state.updated":
		var payload struct {
			Sandbox  json.RawMessage        `json:"sandbox"`
			OldState apiclient.SandboxState `json:"oldState"`
			NewState apiclient.SandboxState `json:"newState"`
		}
		if err := json.Unmarshal(data, &payload); err != nil {
			return
		}
		stateEvent := SandboxStateEvent{OldState: payload.OldState, NewState: payload.NewState}
		// Best effort: waits use only NewState, so a field nothing reads must not drop the transition.
		_ = unmarshalSandboxLenient(payload.Sandbox, &stateEvent.Sandbox)
		event = SandboxEvent{
			Type:       SandboxEventStateUpdated,
			StateEvent: &stateEvent,
		}

	case "sandbox.desired-state.updated":
		var payload struct {
			Sandbox         json.RawMessage               `json:"sandbox"`
			OldDesiredState apiclient.SandboxDesiredState `json:"oldDesiredState"`
			NewDesiredState apiclient.SandboxDesiredState `json:"newDesiredState"`
		}
		if err := json.Unmarshal(data, &payload); err != nil {
			return
		}
		desiredStateEvent := SandboxDesiredStateEvent{
			OldDesiredState: payload.OldDesiredState,
			NewDesiredState: payload.NewDesiredState,
		}
		// Best effort, as for state events: the transition matters more than the snapshot.
		_ = unmarshalSandboxLenient(payload.Sandbox, &desiredStateEvent.Sandbox)
		event = SandboxEvent{
			Type:              SandboxEventDesiredStateUpdated,
			DesiredStateEvent: &desiredStateEvent,
		}

	case "sandbox.created":
		var sandbox apiclient.Sandbox
		if err := json.Unmarshal(data, &sandbox); err != nil {
			return
		}
		event = SandboxEvent{
			Type:         SandboxEventCreated,
			CreatedEvent: &sandbox,
		}

	default:
		return
	}

	es.dispatch(entityID, event)
}

// lenientSandbox has the fields of apiclient.Sandbox but not its UnmarshalJSON,
// which rejects objects that miss any required property.
type lenientSandbox apiclient.Sandbox

// unmarshalSandboxLenient decodes a sandbox object that may carry only a subset
// of its fields (state events can contain just the id and state-related fields).
// Absent fields keep their zero values.
func unmarshalSandboxLenient(data json.RawMessage, out *apiclient.Sandbox) error {
	if len(data) == 0 {
		return nil
	}
	if err := json.Unmarshal(data, out); err == nil {
		return nil
	}
	var sandbox lenientSandbox
	if err := json.Unmarshal(data, &sandbox); err != nil {
		return err
	}
	*out = apiclient.Sandbox(sandbox)
	return nil
}

func extractEntityID(data json.RawMessage) string {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return ""
	}

	nestedKeys := []string{"sandbox", "volume", "snapshot", "runner"}
	for _, key := range nestedKeys {
		if nested, ok := raw[key]; ok {
			var nestedObj map[string]json.RawMessage
			if err := json.Unmarshal(nested, &nestedObj); err == nil {
				if idRaw, ok := nestedObj["id"]; ok {
					var id string
					if err := json.Unmarshal(idRaw, &id); err == nil && id != "" {
						return id
					}
				}
			}
		}
	}

	if idRaw, ok := raw["id"]; ok {
		var id string
		if err := json.Unmarshal(idRaw, &id); err == nil {
			return id
		}
	}

	return ""
}

func (es *EventDispatcher) dispatch(entityID string, event SandboxEvent) {
	if entityID == "" {
		return
	}

	es.mu.RLock()
	handlerMap := es.listeners[entityID]
	handlers := make([]SandboxEventHandler, 0, len(handlerMap))
	for _, h := range handlerMap {
		handlers = append(handlers, h)
	}
	es.mu.RUnlock()

	for _, handler := range handlers {
		func() {
			defer func() {
				_ = recover()
			}()
			handler(event)
		}()
	}
}

func (es *EventDispatcher) handleDisconnect(clientGen uint64) {
	es.mu.Lock()
	if clientGen != es.clientGen {
		// Ignore disconnect from replaced client
		es.mu.Unlock()
		return
	}
	es.connected = false

	select {
	case <-es.closeCh:
		es.mu.Unlock()
		return
	default:
	}

	if es.reconnecting {
		es.mu.Unlock()
		return
	}
	es.reconnecting = true
	es.mu.Unlock()

	go es.reconnectLoop()
}

func (es *EventDispatcher) reconnectLoop() {
	defer func() {
		es.mu.Lock()
		es.reconnecting = false
		es.mu.Unlock()
	}()

	for attempt := 0; attempt < es.maxReconnects; attempt++ {
		select {
		case <-es.closeCh:
			return
		default:
		}

		// Cap the exponent: 1<<5 (32s) already exceeds the 30s ceiling, and an
		// uncapped shift overflows int at attempt>=63 (negative/zero delay).
		exp := attempt
		if exp > 5 {
			exp = 5
		}
		delay := time.Duration(1<<uint(exp)) * time.Second
		if delay > 30*time.Second {
			delay = 30 * time.Second
		}

		select {
		case <-time.After(delay):
		case <-es.closeCh:
			return
		}

		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		err := es.Connect(ctx)
		cancel()

		if err == nil {
			return
		}

		es.mu.Lock()
		es.reconnectAttempt = attempt + 1
		es.mu.Unlock()
	}

	es.mu.Lock()
	es.failed = true
	es.failError = fmt.Sprintf("WebSocket reconnection failed after %d attempts", es.maxReconnects)
	es.mu.Unlock()
}

func (es *EventDispatcher) scheduleDelayedDisconnectLocked() {
	if es.disconnectTimer != nil {
		es.disconnectTimer.Stop()
	}
	myGeneration := es.disconnectGeneration

	es.disconnectTimer = time.AfterFunc(disconnectDelay, func() {
		es.mu.Lock()
		defer es.mu.Unlock()
		if myGeneration == es.disconnectGeneration && len(es.listeners) == 0 {
			es.disconnectLocked(false)
		}
	})
}
