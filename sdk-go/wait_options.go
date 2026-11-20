// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"slices"
	"time"
)

// LifecycleWaitOptions controls completion of a lifecycle operation.
// Timeout defaults to 60 seconds; NoWait returns after acceptance, and
// WaitForever (or a duration above 2,147,483.647 seconds) removes the SDK deadline.
// The context always bounds the operation. Other negative durations are invalid.
type LifecycleWaitOptions struct {
	// Timeout is the completion budget, not the ordinary HTTP request timeout.
	Timeout time.Duration
}

// WaitOptions selects the state and budget for Sandbox.Wait.
type WaitOptions struct {
	// Until defaults to started when nil. An empty slice is invalid.
	// Valid targets are queued, starting, started, pausing, paused, stopping,
	// stopped, destroying and destroyed. Any one target completes the wait.
	Until []SandboxState
	// Timeout defaults to 60 seconds. NoWait skips observation; WaitForever
	// removes the SDK deadline. The context deadline still applies.
	Timeout time.Duration
}

const (
	waitSlice        = 30 * time.Second
	waitReadSlack    = 5 * time.Second
	waitMinInterval  = 200 * time.Millisecond
	waitFinalReserve = 5 * time.Second
	waitEarlyProbe   = 3 * time.Second
)

func lifecycleWaitBudget(value time.Duration) (time.Duration, error) {
	switch {
	case value == 0:
		return time.Minute, nil
	case value == NoWait || value == WaitForever:
		return value, nil
	case value < 0:
		return 0, badRequest("wait budget must be positive, NoWait, or WaitForever")
	case value > maxTimerDuration:
		return WaitForever, nil
	default:
		return value, nil
	}
}

func lifecycleOptions(opts *LifecycleWaitOptions) (time.Duration, error) {
	if opts == nil {
		return lifecycleWaitBudget(0)
	}
	return lifecycleWaitBudget(opts.Timeout)
}

// waitDeadline returns the earlier of the SDK wait budget and the context's
// deadline, and whether the context's deadline is the one that binds.
func waitDeadline(ctx context.Context, budget time.Duration) (time.Time, bool) {
	deadline, _ := ctx.Deadline()
	if budget > 0 {
		local := time.Now().Add(budget)
		if deadline.IsZero() || local.Before(deadline) {
			return local, false
		}
	}
	return deadline, !deadline.IsZero()
}

func waitRemaining(deadline time.Time) time.Duration {
	if deadline.IsZero() {
		return WaitForever
	}
	return max(0, time.Until(deadline))
}

func waitTimeout(cause error) *Error {
	failure := newSDKError(CodeRequestTimeout, 408, "sandbox failed to reach the requested state before timeout")
	failure.Err = cause
	return failure
}

func waitWireState(state SandboxState) (string, error) {
	switch state {
	case SandboxStateQueued:
		return "queued", nil
	case SandboxStateStarting:
		return "booting", nil
	case SandboxStateStarted:
		return "running", nil
	case SandboxStatePausing, SandboxStatePaused, SandboxStateStopping, SandboxStateStopped:
		return string(state), nil
	case SandboxStateDestroying:
		return "deleting", nil
	case SandboxStateDestroyed:
		return "deleted", nil
	case SandboxStateUnknown, SandboxStateCreating, SandboxStateError:
		return "", badRequest("invalid sandbox wait state: " + string(state))
	default:
		return "", badRequest("invalid sandbox wait state: " + string(state))
	}
}

type sandboxWait struct {
	sandbox  *Sandbox
	path     string
	states   []SandboxState
	terminal []SandboxState
	deadline time.Time
	// contextDeadline reports that deadline is the caller's context deadline:
	// reaching it is the caller's context.DeadlineExceeded, not the SDK's
	// wait timeout, even before the context's own timer has fired.
	contextDeadline    bool
	missingIsDestroyed bool
}

func (w *sandboxWait) target(state SandboxState) bool { return slices.Contains(w.states, state) }

func (w *sandboxWait) terminalError(outcome stateObservation) *SandboxTerminalStateError {
	message := "sandbox " + w.sandbox.id + " reached terminal state " + string(outcome.State)
	if outcome.Reason != "" {
		message += ": " + outcome.Reason
	}
	return &SandboxTerminalStateError{
		SDKError: newSDKError("CONFLICT", 409, message),
		State:    outcome.State, RawState: outcome.RawState, Reason: outcome.Reason,
	}
}

func (w *sandboxWait) check(ctx context.Context) error {
	if err := w.sandbox.client.ensureOpen(); err != nil {
		return err
	}
	if ctx.Err() != nil {
		return transportError(ctx, ctx.Err())
	}
	if waitRemaining(w.deadline) == 0 {
		return w.expiredAt(ctx, w.deadline)
	}
	return nil
}

// expiredAt is the error for a wait read bounded by deadline once it has
// passed: the caller's context.DeadlineExceeded when deadline is the wait's
// own deadline and that came from the caller's context (even before the
// context's timer fired), else the SDK's wait timeout.
func (w *sandboxWait) expiredAt(ctx context.Context, deadline time.Time) error {
	if w.contextDeadline && deadline.Equal(w.deadline) {
		return transportError(ctx, context.DeadlineExceeded)
	}
	return waitTimeout(nil)
}
