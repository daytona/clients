// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import "time"

// Opaque destinations and regional tags occupy separate namespaces.
type endpointKey struct {
	value   string
	learned bool
}

func (h *Hub[T]) session(key endpointKey, region string) *session[T] {
	s := h.regions[key]
	if s == nil {
		s = &session[T]{
			hub: h, region: region, wake: make(chan struct{}, 1),
			entries: make(map[string]*entry[T]), dialCancel: func() {},
		}
		h.regions[key] = s
	}
	return s
}

func (s *session[T]) start() {
	if !s.running && !s.permanent && !s.retired {
		s.running = true
		s.hub.workers.Go(s.run)
	}
}

// Learn remembers an opaque destination from a successful sandbox mutation.
// It only groups regional sockets; it is never interpreted as a URL.
func (h *Hub[T]) Learn(region, destination string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.closed || h.ctx.Err() != nil || region == "" || destination == "" {
		return
	}
	previous := h.destinations[region]
	if previous == destination {
		return
	}
	h.destinations[region] = destination
	key := endpointKey{value: region}
	if previous != "" {
		key = endpointKey{value: previous, learned: true}
	}
	destinationKey := endpointKey{value: destination, learned: true}
	source := h.regions[key]
	if source == nil {
		return
	}
	if h.regions[destinationKey] == nil && previous == "" {
		h.regions[destinationKey] = source
		delete(h.regions, key)
		return
	}
	target := h.session(destinationKey, region)
	for id, e := range source.entries {
		if e.region == region && len(e.listeners) > 0 {
			source.move(id, target)
		}
	}
	// Any other region still using this learned socket keeps its mapping.
	// A regional (unlearned) source can only hold this region's entries.
	for _, mapped := range h.destinations {
		if mapped == previous {
			return
		}
	}
	delete(h.regions, key)
	source.retired = true
	source.changed()
	source.dialCancel()
}

func (s *session[T]) move(id string, target *session[T]) {
	e := s.entries[id]
	delete(s.entries, id)
	if e.sent {
		// Leave a tombstone so the next flush drops the old server interest.
		s.entries[id] = &entry[T]{sent: true}
	}
	if held := target.entries[id]; held != nil && len(held.listeners) > 0 {
		for w := range e.listeners {
			held.listeners[w] = struct{}{}
			w.session = target
		}
		held.fresh, held.cached = true, nil
	} else {
		e.sent, e.fresh, e.cached, e.refusedAt = false, true, nil, time.Time{}
		target.entries[id] = e
		for w := range e.listeners {
			w.session = target
		}
	}
	target.idleAt = time.Time{}
	target.start()
	target.changed()
	if !s.active() && s.idleAt.IsZero() {
		s.idleAt = s.hub.clock.Now()
	}
	s.changed()
}

func (s *session[T]) retryRegion(id string) bool {
	e := s.entries[id]
	destination := s.hub.destinations[e.region]
	key := endpointKey{value: destination, learned: true}
	if destination == "" || s.hub.regions[key] != s {
		return false
	}
	delete(s.hub.destinations, e.region)
	target := s.hub.session(endpointKey{value: e.region}, e.region)
	s.move(id, target)
	return true
}
