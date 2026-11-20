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

func (h *Hub[T]) remember(id string) {
	h.sequence++
	h.known[id] = h.sequence
	if len(h.known) <= 10000 {
		return
	}
	oldest, sequence := "", h.sequence
	for key, seen := range h.known {
		if seen < sequence {
			oldest, sequence = key, seen
		}
	}
	delete(h.known, oldest)
}
