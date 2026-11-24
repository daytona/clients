// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import (
	"context"
	"time"

	"github.com/coder/websocket"
)

func (s *session[T]) run() {
	h := s.hub
	for {
		h.mu.Lock()
		if h.ctx.Err() != nil || s.permanent || s.retired || !s.active() {
			s.releaseAll()
			clear(s.entries)
			s.running = false
			// Keep only cached fallback decisions; an idle endpoint needs no
			// parked goroutine, and can be recreated on its next watch.
			if !s.permanent && !h.clock.Now().Before(s.retryAt) {
				for key, current := range h.regions {
					if current == s {
						delete(h.regions, key)
					}
				}
			}
			h.mu.Unlock()
			return
		}
		delay := s.retryAt.Sub(h.clock.Now())
		h.mu.Unlock()
		if delay > 0 && !s.pause(delay) {
			continue
		}
		connection, err := s.connect()
		if err == nil {
			err = s.connected(connection.Socket)
		}
		h.mu.Lock()
		s.failed(connection, err)
		h.mu.Unlock()
	}
}

func (s *session[T]) connect() (Connection, error) {
	h := s.hub
	ctx, cancel := context.WithCancel(h.ctx)
	defer cancel()
	h.mu.Lock()
	s.dialCancel = cancel
	if !s.active() {
		cancel()
	}
	h.mu.Unlock()
	connection, err := h.config.Connect(ctx, s.region)
	h.mu.Lock()
	s.dialCancel = func() {}
	h.mu.Unlock()
	return connection, err
}

func (s *session[T]) failed(connection Connection, err error) {
	h := s.hub
	for id, e := range s.entries {
		e.sent, e.cached, e.fresh = false, nil, true
		if len(e.listeners) == 0 {
			delete(s.entries, id)
		}
	}
	if h.ctx.Err() != nil {
		return
	}
	if connection.Permanent {
		s.permanent = true
		s.releaseAll()
		return
	}
	if err == nil || !s.active() {
		s.attempt, s.refusals = 0, 0
		return
	}
	if websocket.CloseStatus(err) == 4401 {
		s.refusals++
		if s.refusals >= 3 {
			// Polling will surface the authoritative authentication failure.
			s.retryAt = h.clock.Now().Add(fallbackDelay)
			s.releaseAll()
			return
		}
	} else {
		s.refusals = 0
		s.failures++
	}
	delay := max(h.backoff(s.attempt), connection.RetryAfter)
	s.attempt++
	if s.failures >= 3 {
		delay = max(delay, fallbackDelay)
		s.failures = 0
		s.releaseAll()
	}
	s.retryAt = h.clock.Now().Add(delay)
}

func (s *session[T]) pause(delay time.Duration) bool {
	t := s.hub.clock.NewTimer(delay)
	defer t.Stop()
	for {
		select {
		case <-s.hub.ctx.Done():
			return false
		case <-t.C():
			return true
		case <-s.wake:
			s.hub.mu.Lock()
			active := s.active()
			s.hub.mu.Unlock()
			if !active {
				return false
			}
		}
	}
}
