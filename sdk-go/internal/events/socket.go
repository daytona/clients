// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import (
	"context"
	"errors"
	"time"

	"github.com/coder/websocket"
)

type received struct {
	frame frame
	valid bool
	err   error
}

type socketLoop[T any] struct {
	session   *session[T]
	conn      *websocket.Conn
	lastFrame time.Time
	pingSent  bool
	sweepAt   time.Time
	reconcile time.Time
}

func (s *session[T]) connected(conn *websocket.Conn) (failure error) {
	h := s.hub
	ctx, cancel := context.WithCancel(h.ctx)
	defer cancel()
	conn.SetReadLimit(maxServerFrame)
	incoming := make(chan received, 1)
	exited := make(chan struct{})
	go func() {
		defer close(exited)
		readFrames(ctx, conn, incoming)
	}()
	defer func() {
		// CloseNow bounds owned teardown independently of an unresponsive peer.
		// Concurrent CloseNow cannot interrupt coder/websocket's Close handshake.
		failure = errors.Join(failure, conn.CloseNow())
		cancel()
		<-exited
	}()
	now := h.clock.Now()
	loop := socketLoop[T]{
		session: s, conn: conn, lastFrame: now, sweepAt: now.Add(sweepInterval),
		reconcile: now.Add(h.jitter(h.config.ReconcileInterval)),
	}
	h.mu.Lock()
	s.flushAt = now // Reconnect resubscribes every live ID immediately.
	h.mu.Unlock()
	for {
		done, err := loop.tick(ctx)
		if done || err != nil {
			return err
		}
		t := h.clock.NewTimer(loop.nextDelay())
		select {
		case <-ctx.Done():
			t.Stop()
			return ctx.Err()
		case <-s.wake:
		case <-t.C():
		case message := <-incoming:
			t.Stop()
			if message.err != nil {
				return message.err
			}
			loop.lastFrame, loop.pingSent = h.clock.Now(), false
			h.mu.Lock()
			s.failures, s.refusals, s.attempt = 0, 0, 0
			if message.valid {
				s.dispatch(message.frame)
			}
			h.mu.Unlock()
			if message.valid && message.frame.Type == "ping" {
				if sendErr := writeFrame(ctx, conn, []byte(`{"type":"pong"}`)); sendErr != nil {
					return sendErr
				}
			}
		}
		t.Stop()
	}
}

func readFrames(ctx context.Context, conn *websocket.Conn, out chan<- received) {
	for {
		kind, data, err := conn.Read(ctx)
		message := received{err: err}
		if err == nil && kind == websocket.MessageText {
			message.frame, message.valid = decodeFrame(data)
		}
		select {
		case <-ctx.Done():
			return
		case out <- message:
		}
		if err != nil {
			return
		}
	}
}

func (l *socketLoop[T]) tick(ctx context.Context) (bool, error) {
	s, h := l.session, l.session.hub
	now := h.clock.Now()
	h.mu.Lock()
	idle := s.retired || (!s.active() && h.config.IdleClose > 0 &&
		!s.idleAt.IsZero() && !now.Before(s.idleAt.Add(h.config.IdleClose)))
	expired := !now.Before(l.lastFrame.Add(liveness))
	ping := s.active() && !l.pingSent && !now.Before(l.lastFrame.Add(pingAfter))
	l.schedule(now)
	batches := s.pending(now)
	h.mu.Unlock()
	if idle {
		return true, nil
	}
	if expired {
		// A silent peer counts as failure even after a successful upgrade.
		return false, context.DeadlineExceeded
	}
	for _, batch := range batches {
		data, err := subscriptionFrames(batch.kind, batch.ids)
		if err != nil {
			return false, err
		}
		for _, payload := range data {
			if writeErr := writeFrame(ctx, l.conn, payload); writeErr != nil {
				return false, writeErr
			}
		}
	}
	if ping {
		l.pingSent = true
		if err := writeFrame(ctx, l.conn, []byte(`{"type":"ping"}`)); err != nil {
			return false, err
		}
	}
	return false, nil
}

func writeFrame(ctx context.Context, conn *websocket.Conn, payload []byte) error {
	bounded, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	return conn.Write(bounded, websocket.MessageText, payload)
}

func (l *socketLoop[T]) nextDelay() time.Duration {
	s, h := l.session, l.session.hub
	h.mu.Lock()
	defer h.mu.Unlock()
	next := l.lastFrame.Add(liveness)
	consider := func(at time.Time) {
		if !at.IsZero() && at.Before(next) {
			next = at
		}
	}
	consider(s.flushAt)
	if s.active() {
		consider(l.sweepAt)
		consider(l.reconcile)
		if !l.pingSent {
			consider(l.lastFrame.Add(pingAfter))
		}
		for _, e := range s.entries {
			consider(e.refusedAt)
		}
	} else if h.config.IdleClose > 0 && !s.idleAt.IsZero() {
		consider(s.idleAt.Add(h.config.IdleClose))
	}
	return max(0, next.Sub(h.clock.Now()))
}
