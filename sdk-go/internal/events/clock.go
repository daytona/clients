// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import "time"

const (
	flushDelay    = 50 * time.Millisecond
	staleAfter    = 3 * time.Second
	sweepInterval = time.Second
	refusedDelay  = 2 * time.Second
	pingAfter     = 5 * time.Second
	liveness      = 70 * time.Second
	fallbackDelay = time.Minute
	maxTimer      = 2147483647 * time.Millisecond
)

type clock interface {
	Now() time.Time
	NewTimer(time.Duration) timer
}

type timer interface {
	C() <-chan time.Time
	Stop() bool
}

type wallClock struct{}

func (wallClock) Now() time.Time { return time.Now() }
func (wallClock) NewTimer(d time.Duration) timer {
	return wallTimer{time.NewTimer(d)}
}

type wallTimer struct{ *time.Timer }

func (t wallTimer) C() <-chan time.Time { return t.Timer.C }

func (h *Hub[T]) jitter(d time.Duration) time.Duration {
	return time.Duration(float64(d) * (0.9 + 0.2*h.random()))
}

func (h *Hub[T]) backoff(attempt int) time.Duration {
	window := 250 * time.Millisecond * time.Duration(1<<min(attempt, 7))
	return time.Duration(float64(min(window, 30*time.Second)) * h.random())
}
