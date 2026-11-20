// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"io"
	"sync"
	"time"
)

// requestScope owns the one total timer, or the resettable transfer-idle timer.
// Stopping the opening timer leaves caller/client cancellation wired to the body.
type requestScope struct {
	ctx     context.Context
	cancel  context.CancelCauseFunc
	detach  func() bool
	mu      sync.Mutex
	timer   *time.Timer
	budget  time.Duration
	due     time.Time
	stopped bool
}

func newRequestScope(ctx context.Context, client *Client, budget time.Duration) (context.Context, *requestScope) {
	child, cancel := context.WithCancelCause(ctx)
	scope := &requestScope{ctx: child, cancel: cancel, budget: budget}
	// Client shutdown is an additional cancellation source, not a replacement for ctx.
	scope.detach = context.AfterFunc(client.ctx, func() { cancel(closedError()) })
	return child, scope
}

func (s *requestScope) kick() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.budget <= 0 || s.stopped || s.ctx.Err() != nil {
		return
	}
	s.due = time.Now().Add(s.budget)
	if s.timer == nil {
		s.timer = time.AfterFunc(s.budget, s.expire)
	} else {
		s.timer.Reset(s.budget)
	}
}

func (s *requestScope) expire() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.stopped || s.due.IsZero() {
		return
	}
	remaining := time.Until(s.due)
	if remaining > 0 {
		s.timer.Reset(remaining)
		return
	}
	s.cancel(context.DeadlineExceeded)
}

func (s *requestScope) pause() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.due = time.Time{}
	if s.timer != nil {
		s.timer.Stop()
	}
}

func (s *requestScope) close() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.stopped = true
	if s.timer != nil {
		s.timer.Stop()
	}
	s.detach()
	s.cancel(context.Canceled)
}

type responseBody struct {
	io.ReadCloser
	scope *requestScope
	idle  bool
}

func (b *responseBody) Read(data []byte) (int, error) {
	if b.idle {
		b.scope.kick()
	}
	n, err := b.ReadCloser.Read(data)
	if b.idle {
		b.scope.pause()
	}
	if err != nil && !errors.Is(err, io.EOF) {
		return n, scopeError(b.scope, err)
	}
	if errors.Is(err, io.EOF) {
		b.scope.close()
	}
	return n, err
}

func (b *responseBody) Close() error {
	b.scope.close()
	if err := b.ReadCloser.Close(); err != nil {
		return transportError(b.scope.ctx, err)
	}
	return nil
}

type uploadBody struct {
	io.ReadCloser
	scope *requestScope
}

func (b *uploadBody) Read(data []byte) (int, error) {
	n, err := b.ReadCloser.Read(data)
	if n > 0 {
		b.scope.kick()
	}
	return n, err
}

type idleReadCloser struct {
	io.ReadCloser
	scope *requestScope
}

func (b *idleReadCloser) Read(data []byte) (int, error) {
	n, err := b.ReadCloser.Read(data)
	if n > 0 {
		b.scope.kick()
	}
	if err != nil && !errors.Is(err, io.EOF) {
		return n, scopeError(b.scope, err)
	}
	return n, err
}

func scopeError(scope *requestScope, err error) error {
	if errors.Is(context.Cause(scope.ctx), context.DeadlineExceeded) {
		result := newSDKError("REQUEST_TIMEOUT", 408, "operation timed out")
		result.Err = context.DeadlineExceeded
		result.category = ErrClientTimeout
		return result
	}
	return transportError(scope.ctx, err)
}
