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
// Events can wake the wait early; a bounded wait reserves up to five seconds
// for authoritative API reads. Once a target event was observed, failure to
// refresh metadata does not undo success. Caller cancellation is never hidden.
func (s *Sandbox) Wait(ctx context.Context, opts *WaitOptions) error {
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
	if err = s.client.ensureOpen(); err != nil {
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
	if events == nil {
		return w.poll(ctx)
	}
	decided, err := w.withEvents(ctx, events)
	if decided {
		return err
	}
	finalReserve := errors.Is(err, ErrRequestTimeout)
	if pollErr := w.poll(ctx); pollErr != nil {
		if finalReserve && !waitReadDecisive(pollErr) {
			return waitTimeout(pollErr)
		}
		return pollErr
	}
	return nil
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

func (w *sandboxWait) poll(ctx context.Context) error {
	for {
		if err := w.check(ctx); err != nil {
			return err
		}
		started := time.Now()
		outcome, err := w.read(ctx, waitReadOptions{deadline: w.deadline, longPoll: true})
		if err != nil {
			return err
		}
		if w.target(outcome.State) {
			return nil
		}
		if slices.Contains(w.terminal, outcome.State) {
			return w.terminalError(outcome)
		}
		if err = w.pause(ctx, waitMinInterval-time.Since(started)); err != nil {
			return err
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
