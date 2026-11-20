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
// NoWait skips settling and completion; for an in-progress stop/pause it sends
// start immediately and lets the API decide whether it can accept the request.
// Timeout returns ErrRequestTimeout; terminal failures return *SandboxTerminalStateError.
func (s *Sandbox) Start(ctx context.Context, opts *LifecycleWaitOptions) error {
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
	case SandboxStateStopping, SandboxStatePausing:
		if acceptedOnly {
			action = "start"
		}
	case SandboxStateQueued, SandboxStateCreating, SandboxStateStarting,
		SandboxStateDestroying, SandboxStateDestroyed, SandboxStateError, SandboxStateUnknown:
		// These states already have work in flight, or will fail the wait.
	}
	if action != "" {
		if err := s.client.api().do(ctx, apiRequest{Method: http.MethodPost, Path: w.path + "/" + action}); err != nil {
			return err
		}
	}
	if acceptedOnly {
		return nil
	}
	return w.run(ctx)
}
