// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"time"

	"go.daytona.com/sdk/v2/internal/events"
)

type sandboxWatch struct {
	events   <-chan stateObservation
	activity events.WatchLiveness
}

type waitProbe struct {
	watch      sandboxWatch
	delay      time.Duration
	shortFirst bool
	retry      *waitReadRetry
}

func newWaitProbe(watch sandboxWatch, deadline time.Time, retry *waitReadRetry) waitProbe {
	delay := waitEarlyProbe
	if remaining := waitRemaining(deadline); remaining >= 0 {
		delay = min(delay, remaining/2)
	}
	return waitProbe{
		watch: watch, delay: watch.activity.Jitter(delay), shortFirst: delay < waitEarlyProbe, retry: retry,
	}
}

func (p *waitProbe) ready() (events.FrameStamp, bool) {
	frame := p.watch.activity.Frame()
	quiet := p.watch.silentFor(frame) >= waitEarlyProbe
	if p.shortFirst {
		quiet = !frame.LastFrameAt.After(p.watch.activity.RegisteredAt)
		p.shortFirst = false
	}
	if !quiet {
		p.delay = p.watch.quietDelay(0)
	}
	return frame, quiet
}

func (p *waitProbe) accept(ctx context.Context, w *sandboxWait, result waitReadResult) (bool, error) {
	outcome, err := w.accept(result)
	if err != nil {
		if stopped := w.retryReadError(ctx, err, p.retry); stopped != nil {
			return true, stopped
		}
		p.delay = p.watch.activity.Jitter(waitEarlyProbe)
		return false, nil
	}
	p.retry.succeeded()
	if decided, outcomeErr := w.readOutcome(outcome); decided {
		return true, outcomeErr
	}
	p.delay = p.watch.quietDelay(waitMinInterval)
	return false, nil
}

func (s sandboxWatch) silentFor(frame events.FrameStamp) time.Duration {
	last := frame.LastFrameAt
	if last.Before(s.activity.RegisteredAt) {
		last = s.activity.RegisteredAt
	}
	return time.Since(last)
}

// quietDelay measures silence from this wait's registration, never from an
// older wait on the shared socket. A nonterminal poll also gets minimum spacing.
func (s sandboxWatch) quietDelay(minimum time.Duration) time.Duration {
	delay := max(minimum, waitEarlyProbe-s.silentFor(s.activity.Frame()))
	if delay <= 0 {
		return 0
	}
	// Go timers have nanosecond precision; rounding down must not spin forever
	// just before the quiet threshold when jitter selects a smaller delay.
	return max(time.Nanosecond, s.activity.Jitter(delay))
}
