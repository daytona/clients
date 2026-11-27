// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
)

// Start reads the current state, starts a stopped sandbox or resumes a paused
// sandbox, then waits for started. An in-progress stop/pause first settles under
// the same budget (default 60 seconds). Already-started sandboxes need no action.
// A sandbox already starting (a start or resume another caller sent) is joined
// without a request; its return to stopped or paused is a
// *SandboxTerminalStateError once the wait's long-poll reads confirm either
// state (up to one 30-second slice). A macOS sandbox is read once more after
// the events stream reports started, while budget remains, for its boot mode
// and reservation; a failed read keeps the observed success. Other fields,
// SpotEvictedAt and the lifecycle-policy deadlines included, are as of the
// last read.
// NoWait skips settling and completion; for an in-progress stop/pause it sends
// start immediately and lets the API decide whether it can accept the request.
// Timeout returns ErrRequestTimeout; terminal failures return *SandboxTerminalStateError.
func (s *Sandbox) Start(ctx context.Context, opts *LifecycleWaitOptions) error {
	if err := s.client.ensureOpen(); err != nil {
		return err
	}
	budget, err := lifecycleOptions(opts)
	if err != nil {
		return err
	}
	w, err := s.newWait([]SandboxState{SandboxStateStarted})
	if err != nil {
		return err
	}
	w.deadline, w.contextDeadline = waitDeadline(ctx, budget)
	outcome, err := w.read(ctx, waitReadOptions{deadline: w.deadline})
	if err != nil {
		return err
	}
	switch outcome.State {
	case SandboxStateStopping, SandboxStatePausing:
		if budget != NoWait {
			settling := *w
			settling.states = []SandboxState{SandboxStateStarted, SandboxStatePaused, SandboxStateStopped}
			if err = settling.run(ctx); err != nil {
				return err
			}
			if err = w.check(ctx); err != nil {
				return err
			}
		}
	case SandboxStateQueued, SandboxStateCreating, SandboxStateStarting, SandboxStateStarted,
		SandboxStatePaused, SandboxStateStopped, SandboxStateDestroying, SandboxStateDestroyed,
		SandboxStateError, SandboxStateUnknown:
		// The current state below selects the action, including future states.
	}
	return s.startObserved(ctx, w, budget == NoWait)
}

func (s *Sandbox) startObserved(ctx context.Context, w *sandboxWait, acceptedOnly bool) error {
	action := ""
	switch s.State() {
	case SandboxStateStarted:
		return nil
	case SandboxStatePaused:
		action = "resume"
		w.terminal = append(w.terminal, SandboxStateStopped)
	case SandboxStateStopped:
		action = "start"
	case SandboxStateStarting:
		// Joining a start or resume another caller sent: when it fails, the
		// sandbox goes back to stopped or paused.
		w.terminal = append(w.terminal, SandboxStateStopped, SandboxStatePaused)
	case SandboxStateStopping, SandboxStatePausing:
		if acceptedOnly {
			action = "start"
		}
	case SandboxStateQueued, SandboxStateCreating,
		SandboxStateDestroying, SandboxStateDestroyed, SandboxStateError, SandboxStateUnknown:
		// These states already have work in flight, or will fail the wait.
	}
	if action != "" {
		if err := w.check(ctx); err != nil {
			return err
		}
		if err := s.client.api().do(ctx, apiRequest{Method: http.MethodPost, Path: w.path + "/" + action}); err != nil {
			return err
		}
	}
	// A macOS start can change the boot mode and the reservation: one read
	// after the target event fills them in.
	w.readAfterMacOSTarget = true
	if acceptedOnly {
		info := s.Info()
		_, err := w.readOutcome(stateObservation{State: info.State, RawState: info.RawState, Reason: info.Error})
		return err
	}
	return w.run(ctx)
}
