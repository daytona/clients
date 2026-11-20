// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"slices"
	"time"
)

// withEvents gives the source all but the final-read reserve. Probes are gated
// by socket silence; a closed source falls back immediately. Every probe is
// joined before using the reserve.
func (w *sandboxWait) withEvents(
	ctx context.Context, watch sandboxWatch, retry *waitReadRetry,
) (bool, error) {
	share := w.deadline
	if remaining := waitRemaining(share); remaining >= 0 {
		share = share.Add(-min(waitFinalReserve, remaining/4))
	}
	probe := newWaitProbe(watch, share, retry)
	for {
		event, open, err := w.awaitEvents(ctx, watch.events, waitEventWindow{deadline: share, duration: probe.delay})
		if err != nil {
			return true, err
		}
		if !open {
			return false, nil
		}
		if event != nil {
			return w.confirmEvent(ctx, *event)
		}
		if waitRemaining(share) == 0 {
			return false, waitTimeout(nil)
		}
		before, quiet := probe.ready()
		if !quiet {
			continue
		}
		result := w.raceRead(ctx, watch.events, share)
		if result.event != nil {
			return w.confirmEvent(ctx, *result.event)
		}
		if result.closed {
			return false, nil
		}
		// Like the reference hub, a frame or reroute supersedes the poll.
		if result.read.err == nil && before != watch.activity.Frame() {
			probe.delay = watch.quietDelay(0)
			continue
		}
		if waitRemaining(share) == 0 && waitSDKTimeout(result.read.err) {
			return false, waitTimeout(nil)
		}
		if decided, probeErr := probe.accept(ctx, w, result.read); decided {
			return true, probeErr
		}
	}
}

type waitEventWindow struct {
	deadline time.Time
	duration time.Duration
}

func (w *sandboxWait) awaitEvents(
	ctx context.Context, events <-chan stateObservation, window waitEventWindow,
) (*stateObservation, bool, error) {
	delay := window.duration
	if remaining := waitRemaining(window.deadline); remaining >= 0 {
		delay = min(delay, remaining)
	}
	timer := time.NewTimer(delay)
	defer timer.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil, true, transportError(ctx, ctx.Err())
		case <-w.sandbox.client.ctx.Done():
			return nil, true, closedError()
		case <-timer.C:
			return nil, true, nil
		case event, ok := <-events:
			if !ok {
				return nil, false, nil
			}
			if event.NotFound || w.target(event.State) || slices.Contains(w.terminal, event.State) {
				return &event, true, nil
			}
			w.sandbox.observeWait(event)
		}
	}
}

type waitRaceResult struct {
	read   waitReadResult
	event  *stateObservation
	closed bool
}

func (w *sandboxWait) raceRead(
	ctx context.Context, events <-chan stateObservation, deadline time.Time,
) waitRaceResult {
	readCtx, cancel := context.WithCancel(ctx)
	done := make(chan waitReadResult, 1)
	go func() { done <- w.fetch(readCtx, waitReadOptions{deadline: deadline, longPoll: true}) }()
	joined := false
	defer func() {
		cancel()
		if !joined {
			<-done
		}
	}()
	for {
		select {
		case result := <-done:
			joined = true
			return waitRaceResult{read: result}
		case <-ctx.Done():
			return waitRaceResult{read: waitReadResult{err: transportError(ctx, ctx.Err())}}
		case <-w.sandbox.client.ctx.Done():
			return waitRaceResult{read: waitReadResult{err: closedError()}}
		case event, ok := <-events:
			if !ok {
				return waitRaceResult{closed: true}
			}
			if event.NotFound || w.target(event.State) || slices.Contains(w.terminal, event.State) {
				return waitRaceResult{event: &event}
			}
			w.sandbox.observeWait(event)
		}
	}
}

// A terminal event can predate this operation. Only a full read decides that
// failure; a successful target event needs just a best-effort metadata refresh.
// A terminal disappearance is final and must not be replaced by an API read.
func (w *sandboxWait) confirmEvent(ctx context.Context, event stateObservation) (bool, error) {
	if event.NotFound {
		if w.missingIsDestroyed {
			w.sandbox.observeWait(stateObservation{State: SandboxStateDestroyed, RawState: "deleted"})
			return true, nil
		}
		return true, newSDKError(CodeNotFound, 404,
			"sandbox "+w.sandbox.id+" is no longer present; its final state was not retained")
	}
	reported := event
	if reported.Reason == "" {
		reported.Reason = w.sandbox.readReasonFor(event)
	}
	w.sandbox.observeWait(reported)
	success := w.target(event.State)
	if event.State == SandboxStateDestroyed || waitRemaining(w.deadline) == 0 {
		return w.readOutcome(reported)
	}
	fresh, err := w.read(ctx, waitReadOptions{deadline: w.deadline})
	if err != nil && (ctx.Err() != nil || errors.Is(err, ErrClientClosed)) {
		return true, err
	}
	if success {
		// All read failures are SDK errors at this boundary. Success survives.
		return true, nil
	}
	if err != nil {
		return true, w.terminalError(reported)
	}
	if w.target(fresh.State) {
		return true, nil
	}
	if slices.Contains(w.terminal, fresh.State) {
		return true, w.terminalError(fresh)
	}
	// A stale terminal event is discarded; poll for the rest of the budget.
	return false, nil
}
