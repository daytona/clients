// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "context"

// All queue fields are protected by the service mutex. Pending callers receive
// individual permits, so cancellation removes a waiter without barging.
type interpreterQueue struct {
	running bool
	closing bool
	waiters []*interpreterWaiter
}

type interpreterWaiter struct {
	ready   chan struct{}
	granted bool
}

func (s *Interpreter) acquire(ctx context.Context, id string) (func(), error) {
	s.mu.Lock()
	queue := s.queues[id]
	if queue == nil {
		queue = &interpreterQueue{}
		s.queues[id] = queue
	}
	waiter := &interpreterWaiter{ready: make(chan struct{})}
	if !queue.running {
		queue.running, waiter.granted = true, true
		close(waiter.ready)
	} else {
		queue.waiters = append(queue.waiters, waiter)
	}
	s.mu.Unlock()
	select {
	case <-waiter.ready:
		return func() { s.releaseQueue(id, queue) }, nil
	case <-ctx.Done():
		s.cancelWaiter(id, queue, waiter)
		return nil, transportError(ctx, ctx.Err())
	case <-s.sandbox.client.ctx.Done():
		s.cancelWaiter(id, queue, waiter)
		return nil, closedError()
	}
}

func (s *Interpreter) cancelWaiter(id string, queue *interpreterQueue, waiter *interpreterWaiter) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if waiter.granted {
		s.advanceQueue(id, queue)
		return
	}
	for index, candidate := range queue.waiters {
		if candidate == waiter {
			queue.waiters = append(queue.waiters[:index], queue.waiters[index+1:]...)
			break
		}
	}
}

func (s *Interpreter) releaseQueue(id string, queue *interpreterQueue) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.advanceQueue(id, queue)
}

func (s *Interpreter) advanceQueue(id string, queue *interpreterQueue) {
	if len(queue.waiters) > 0 {
		next := queue.waiters[0]
		queue.waiters[0] = nil
		queue.waiters = queue.waiters[1:]
		next.granted = true
		close(next.ready)
		return
	}
	queue.running = false
	if queue.closing && s.queues[id] == queue {
		delete(s.queues, id)
	}
}

func (s *Interpreter) evictQueue(id string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	queue := s.queues[id]
	if queue == nil {
		return
	}
	queue.closing = true
	if !queue.running && len(queue.waiters) == 0 {
		delete(s.queues, id)
	}
}
