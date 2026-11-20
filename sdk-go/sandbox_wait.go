// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"slices"
	"strings"
	"time"
)

// Wait observes the server until any requested state is reached (default started).
// A spent budget returns ErrRequestTimeout with source SDK; an incompatible
// terminal state returns *SandboxTerminalStateError, including its raw status
// and any reason read from the server. Unknown server states keep polling.
// An event reporting a known sandbox's disappearance returns ErrNotFound with source SDK.
// Events can wake the wait early; a bounded wait reserves up to five seconds
// for authoritative API reads. Once a target event was observed, failure to
// refresh metadata does not undo success. Caller cancellation is never hidden.
// With events enabled, nondecisive read failures (including rate limits) retry
// within the budget, honoring RetryAfter; the last read failure is retained
// as a timeout cause.
// Fallback polling stops on HTTP 4xx other than 429; live-event probes stop
// only on 401, 403, or 404. Final-reserve failures remain timeout causes.
// With events disabled, read failures are returned immediately.
func (s *Sandbox) Wait(ctx context.Context, opts *WaitOptions) error {
	if err := s.client.ensureOpen(); err != nil {
		return err
	}
	options := WaitOptions{}
	if opts != nil {
		options = *opts
	}
	states := options.Until
	if states == nil {
		states = []SandboxState{SandboxStateStarted}
	}
	budget, err := lifecycleWaitBudget(options.Timeout)
	if err != nil {
		return err
	}
	w, err := s.newWait(states)
	if err != nil {
		return err
	}
	if ctx.Err() != nil {
		return transportError(ctx, ctx.Err())
	}
	if budget == NoWait {
		return nil
	}
	w.deadline, w.contextDeadline = waitDeadline(ctx, budget)
	return w.run(ctx)
}

func (s *Sandbox) newWait(states []SandboxState) (*sandboxWait, error) {
	if len(states) == 0 {
		return nil, badRequest("wait requires at least one target state")
	}
	for _, state := range states {
		if _, err := waitWireState(state); err != nil {
			return nil, err
		}
	}
	segment, err := pathSegment(s.id)
	if err != nil {
		return nil, err
	}
	terminal := []SandboxState{SandboxStateDestroyed, SandboxStateError}
	if !slices.Contains(states, SandboxStateDestroyed) {
		terminal = append(terminal, SandboxStateDestroying)
	}
	return &sandboxWait{
		sandbox: s, path: "/sandboxes/" + segment, states: slices.Clone(states), terminal: terminal,
	}, nil
}

func (w *sandboxWait) run(ctx context.Context) error {
	if err := w.check(ctx); err != nil {
		return err
	}
	region := w.sandbox.Info().Region
	if strings.EqualFold(strings.TrimSpace(region), "local") {
		region = ""
	}
	events, release := w.sandbox.client.stateSource.watch(ctx, w.sandbox.id, region)
	defer release()
	retry := newWaitReadRetry(w.sandbox.client.stateSource)
	if events == nil {
		return w.poll(ctx, &retry)
	}
	decided, err := w.withEvents(ctx, events, &retry)
	if decided {
		return retry.finish(err)
	}
	pollErr := w.poll(ctx, &retry)
	if waitSDKTimeout(err) && pollErr != nil && !waitReadDecisive(pollErr) {
		return waitTimeout(pollErr)
	}
	return pollErr
}

func waitReadDecisive(err error) bool {
	var terminal *SandboxTerminalStateError
	var sdkError *Error
	return errors.As(err, &terminal) || errors.Is(err, ErrNotFound) ||
		errors.Is(err, ErrAuthentication) || errors.Is(err, ErrForbidden) ||
		errors.Is(err, ErrClientClosed) || errors.Is(err, context.Canceled) ||
		errors.Is(err, context.DeadlineExceeded) ||
		(errors.As(err, &sdkError) && sdkError.Code == CodeRequestTimeout && sdkError.Source == "SDK")
}

func (w *sandboxWait) poll(ctx context.Context, retry *waitReadRetry) error {
	retry.fallback = true
	for {
		if err := w.pause(ctx, retry.remaining()); err != nil {
			return retry.finish(err)
		}
		started := time.Now()
		outcome, err := w.read(ctx, waitReadOptions{deadline: w.deadline, longPoll: true})
		if err != nil {
			if stopped := w.retryReadError(ctx, err, retry); stopped != nil {
				return retry.finish(stopped)
			}
			continue
		}
		retry.succeeded()
		if decided, outcomeErr := w.readOutcome(outcome); decided {
			return outcomeErr
		}
		if err = w.pause(ctx, waitMinInterval-time.Since(started)); err != nil {
			return retry.finish(err)
		}
	}
}

func (w *sandboxWait) pause(ctx context.Context, duration time.Duration) error {
	if remaining := waitRemaining(w.deadline); remaining >= 0 {
		duration = min(duration, remaining)
	}
	if duration <= 0 {
		return w.check(ctx)
	}
	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return transportError(ctx, ctx.Err())
	case <-w.sandbox.client.ctx.Done():
		return closedError()
	case <-timer.C:
		return nil
	}
}
