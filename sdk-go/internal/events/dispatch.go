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
		if s.retryRegion(id) {
			return
		}
		fallthrough
	case "MALFORMED":
		// The core seam carries observations, not errors. A full API read
		// decides missing/unauthorized/malformed IDs, never a fabricated state.
		for w := range e.listeners {
			s.release(id, w)
		}
	default:
		// Unknown error codes are inert, just like unknown frame types.
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
