// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import (
	"context"
	"encoding/json"
	"math/rand/v2"
	"sync"
	"time"

	"github.com/coder/websocket"
)

// Update is a state observed on the socket, not an authoritative API read.
type Update struct {
	Status string
	Reason string
	At     time.Time
}

// Connection includes the retry policy of a failed ticket mint or upgrade.
// Socket is non-nil on success. Permanent disables this regional endpoint.
type Connection struct {
	Socket     *websocket.Conn
	Permanent  bool
	RetryAfter time.Duration
}

// Config binds a hub to its owner's transport and observation type.
// Connect must honor ctx. Observe must be pure and nonblocking.
// Zero ReconcileInterval uses one minute; zero IdleClose keeps sockets open.
type Config[T any] struct {
	Connect           func(context.Context, string) (Connection, error)
	Observe           func(Update) T
	ReconcileInterval time.Duration
	IdleClose         time.Duration
}

// Hub multiplexes concurrent watches onto regional sockets. Construct with New.
// A nil or closed watch channel signals polling fallback, never a sandbox state.
type Hub[T any] struct {
	mu           sync.Mutex
	ctx          context.Context
	cancel       context.CancelFunc
	workers      sync.WaitGroup
	config       Config[T]
	clock        clock
	random       func() float64
	closed       bool
	regions      map[endpointKey]*session[T]
	destinations map[string]string
	known        map[string]*knownID
	oldest       *knownID
	newest       *knownID
}

type listener[T any] struct {
	out     chan T
	stop    func() bool
	session *session[T]
}

type entry[T any] struct {
	region    string
	listeners map[*listener[T]]struct{}
	version   float64
	pending   bool
	sent      bool
	fresh     bool
	cached    *Update
	heardAt   time.Time
	refusedAt time.Time
}

type session[T any] struct {
	hub        *Hub[T]
	region     string
	wake       chan struct{}
	entries    map[string]*entry[T]
	permanent  bool
	retired    bool
	running    bool
	retryAt    time.Time
	flushAt    time.Time
	idleAt     time.Time
	dialCancel context.CancelFunc
	failures   int
	refusals   int
	attempt    int
}

// New creates a lazy hub; it opens no socket until the first Watch.
func New[T any](ctx context.Context, config Config[T]) *Hub[T] {
	//nolint:gosec // G118: this owned lifetime is canceled by Hub.Close, not by the constructor.
	owned, cancel := context.WithCancel(ctx)
	if config.ReconcileInterval == 0 {
		config.ReconcileInterval = time.Minute
	}
	config.ReconcileInterval = min(config.ReconcileInterval, maxTimer*10/11)
	config.IdleClose = min(config.IdleClose, maxTimer)
	return &Hub[T]{
		ctx: owned, cancel: cancel, config: config, clock: wallClock{}, random: rand.Float64,
		regions: make(map[endpointKey]*session[T]), known: make(map[string]*knownID),
		destinations: make(map[string]string),
	}
}

// Watch subscribes until ctx ends or release is called. Release is idempotent.
// Slow consumers receive the latest observation, not an unbounded event backlog.
func (h *Hub[T]) Watch(ctx context.Context, id, region string) (<-chan T, func()) {
	encoded, err := json.Marshal(subscription{ID: id})
	if err != nil || len(encoded)+32 > maxClientFrame {
		return nil, func() {}
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.closed || h.ctx.Err() != nil || ctx.Err() != nil {
		return nil, func() {}
	}
	key := endpointKey{value: region}
	if destination := h.destinations[region]; destination != "" {
		key = endpointKey{value: destination, learned: true}
	}
	s := h.session(key, region)
	if s.permanent || h.clock.Now().Before(s.retryAt) {
		return nil, func() {}
	}
	e := s.entries[id]
	if e == nil {
		e = &entry[T]{region: region, listeners: make(map[*listener[T]]struct{}), version: -1}
		s.entries[id] = e
	}
	if len(e.listeners) == 0 {
		e.fresh, e.cached, e.version = true, nil, -1
	}
	w := &listener[T]{out: make(chan T, 1), session: s}
	e.listeners[w] = struct{}{}
	s.idleAt = time.Time{}
	if e.cached != nil && !e.pending {
		w.out <- h.config.Observe(*e.cached)
	}
	release := func() {
		h.mu.Lock()
		defer h.mu.Unlock()
		w.session.release(id, w)
	}
	// Account for cancellation callbacks as well as socket workers: Close must
	// join a callback that already started, not only unregister pending ones.
	h.workers.Add(1)
	stop := context.AfterFunc(ctx, func() {
		defer h.workers.Done()
		release()
	})
	w.stop = func() bool {
		if stop() {
			h.workers.Done()
			return true
		}
		return false
	}
	s.start()
	s.changed()
	return w.out, release
}

// Close cancels connection work, closes watches and joins every socket worker.
// It is safe to call concurrently with Watch or another Close.
func (h *Hub[T]) Close() {
	h.mu.Lock()
	h.closed = true
	h.cancel()
	for _, s := range h.regions {
		s.releaseAll()
	}
	h.mu.Unlock()
	h.workers.Wait()
}

func (s *session[T]) changed() {
	if s.flushAt.IsZero() {
		s.flushAt = s.hub.clock.Now().Add(flushDelay)
	}
	select {
	case s.wake <- struct{}{}:
	default:
	}
}

func (s *session[T]) release(id string, w *listener[T]) {
	e := s.entries[id]
	if e == nil {
		return
	}
	if _, exists := e.listeners[w]; !exists {
		return
	}
	delete(e.listeners, w)
	w.stop()
	close(w.out)
	if len(e.listeners) == 0 {
		e.cached = nil
		s.changed()
	}
	if !s.active() {
		if s.idleAt.IsZero() {
			s.idleAt = s.hub.clock.Now()
		}
		s.dialCancel()
	}
}

func (s *session[T]) active() bool {
	for _, e := range s.entries {
		if len(e.listeners) > 0 {
			return true
		}
	}
	return false
}

func (s *session[T]) releaseAll() {
	for id, e := range s.entries {
		for w := range e.listeners {
			s.release(id, w)
		}
	}
}
