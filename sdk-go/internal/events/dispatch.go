// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import "time"

func (s *session[T]) dispatch(f frame) {
	switch f.Type {
	case "snapshot", "event":
		s.dispatchState(f)
	case "error":
		s.dispatchError(f)
	case "ping", "pong":
		// Socket liveness and ping responses belong to the connection loop.
	default:
		// Unknown frame names are forward-compatible extensions.
	}
}

func (s *session[T]) dispatchState(f frame) {
	if f.ID == nil {
		return
	}
	id := *f.ID
	s.hub.remember(id)
	e := s.entries[id]
	if e == nil || len(e.listeners) == 0 {
		return
	}
	if f.Type == "event" && e.pending && f.Version <= e.version {
		return
	}
	e.pending = false
	if f.Version < e.version {
		return
	}
	e.version, e.heardAt, e.refusedAt = f.Version, s.hub.clock.Now(), time.Time{}
	update := Update{Status: f.Status, Reason: f.Reason, At: e.heardAt}
	e.cached = &update
	s.publish(e, update)
}

func (s *session[T]) publish(e *entry[T], update Update) {
	for w := range e.listeners {
		// Only this producer touches the buffer under the hub lock.
		select {
		case <-w.out:
		default:
		}
		w.out <- s.hub.config.Observe(update)
	}
}

func (s *session[T]) dispatchError(f frame) {
	if f.Code == "LIMIT" && f.ID == nil {
		for _, e := range s.entries {
			if len(e.listeners) > 0 && e.refusedAt.IsZero() {
				e.refusedAt = s.hub.clock.Now().Add(refusedDelay)
			}
		}
		return
	}
	if f.ID == nil {
		return
	}
	id := *f.ID
	e := s.entries[id]
	if e == nil || len(e.listeners) == 0 {
		return
	}
	switch f.Code {
	case "LIMIT":
		if e.refusedAt.IsZero() {
			e.refusedAt = s.hub.clock.Now().Add(refusedDelay)
		}
	case "NOT_FOUND":
		s.dispatchNotFound(id, f.WasKnown)
	case "MALFORMED":
		// An API read decides malformed IDs.
		for w := range e.listeners {
			s.release(id, w)
		}
	default:
		// Unknown error codes are inert, just like unknown frame types.
	}
}

func (s *session[T]) dispatchNotFound(id string, wasKnown bool) {
	if s.retryRegion(id) {
		return
	}
	e := s.entries[id]
	if wasKnown || s.hub.known[id] != nil || e.version >= 0 {
		s.publish(e, Update{NotFound: true, At: s.hub.clock.Now()})
	}
	// Without a terminal update, closing the watch requests an API read
	// for an initially invisible ID.
	for w := range e.listeners {
		s.release(id, w)
	}
}

// knownID links the bounded history in least-to-most-recent observation order.
// All links and the lookup map are protected by the hub lock.
type knownID struct {
	id             string
	previous, next *knownID
}

func (h *Hub[T]) remember(id string) {
	entry := h.known[id]
	switch entry {
	case nil:
		entry = &knownID{id: id}
		h.known[id] = entry
	case h.newest:
		return
	default:
		if entry.previous == nil {
			h.oldest = entry.next
		} else {
			entry.previous.next = entry.next
		}
		entry.next.previous = entry.previous
	}
	entry.previous, entry.next = h.newest, nil
	if h.newest == nil {
		h.oldest = entry
	} else {
		h.newest.next = entry
	}
	h.newest = entry
	if len(h.known) > 10000 {
		delete(h.known, h.oldest.id)
		h.oldest = h.oldest.next
		h.oldest.previous = nil
	}
}
