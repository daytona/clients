// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import (
	"slices"
	"time"
)

type batch struct {
	kind string
	ids  []subscription
}

func (l *socketLoop[T]) schedule(now time.Time) {
	s := l.session
	if !s.active() {
		return
	}
	if !now.Before(l.reconcile) {
		for _, e := range s.entries {
			if len(e.listeners) > 0 {
				e.fresh = true
			}
		}
		s.flushAt = now
		l.reconcile = now.Add(s.hub.jitter(s.hub.config.ReconcileInterval))
	}
	if !now.Before(l.sweepAt) {
		s.sweep(now)
		l.sweepAt = now.Add(sweepInterval)
	}
	retries := 0
	for _, e := range s.entries {
		if len(e.listeners) == 0 || e.refusedAt.IsZero() || now.Before(e.refusedAt) {
			continue
		}
		if retries >= maxFrameIDs {
			e.refusedAt = now.Add(refusedDelay)
			continue
		}
		e.refusedAt, e.fresh, s.flushAt = time.Time{}, true, now
		retries++
	}
}

func (s *session[T]) sweep(now time.Time) {
	stale := make([]string, 0, len(s.entries))
	for id, e := range s.entries {
		if len(e.listeners) > 0 && !e.fresh && e.refusedAt.IsZero() && now.Sub(e.heardAt) >= staleAfter {
			stale = append(stale, id)
		}
	}
	slices.SortFunc(stale, func(a, b string) int { return s.entries[a].heardAt.Compare(s.entries[b].heardAt) })
	for _, id := range stale[:min(len(stale), 1500)] {
		s.entries[id].fresh = true
		s.flushAt = now
	}
}

func (s *session[T]) pending(now time.Time) []batch {
	if s.flushAt.IsZero() || now.Before(s.flushAt) {
		return nil
	}
	s.flushAt = time.Time{}
	subscribe := batch{kind: "subscribe"}
	unsubscribe := batch{kind: "unsubscribe"}
	for id, e := range s.entries {
		if len(e.listeners) == 0 {
			if e.sent {
				unsubscribe.ids = append(unsubscribe.ids, subscription{ID: id})
			}
			delete(s.entries, id)
			continue
		}
		if e.fresh {
			_, known := s.hub.known[id]
			subscribe.ids = append(subscribe.ids, subscription{ID: id, Known: known})
			e.pending, e.sent, e.fresh, e.heardAt = true, true, false, now
		}
	}
	// Free capacity before accepting fresh subscriptions.
	return []batch{unsubscribe, subscribe}
}
