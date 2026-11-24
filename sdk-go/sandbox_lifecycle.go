// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
)

// Stop stops the sandbox and waits for stopped. The default wait is 60 seconds;
// NoWait returns after acceptance. Toolbox calls require Start before reuse.
// A failed completion returns ErrRequestTimeout or *SandboxTerminalStateError.
func (s *Sandbox) Stop(ctx context.Context, opts *LifecycleWaitOptions) error {
	return s.transition(ctx, lifecycleTransition{action: "stop", target: SandboxStateStopped}, opts)
}

// Pause saves sandbox memory and waits for paused (default 60 seconds).
// Returning to started is a *SandboxTerminalStateError. NoWait skips waiting.
// The server decides whether the sandbox supports pausing.
func (s *Sandbox) Pause(ctx context.Context, opts *LifecycleWaitOptions) error {
	return s.transition(ctx, lifecycleTransition{
		action: "pause", target: SandboxStatePaused, failure: SandboxStateStarted,
	}, opts)
}

// Resume resumes a paused sandbox and waits for started (default 60 seconds).
// Returning to stopped is a *SandboxTerminalStateError. NoWait skips waiting.
//
// Whether the sandbox can resume is the API's decision; a refusal returns its
// typed error. A sandbox that is not paused, or whose saved state is
// unavailable, returns ErrConflict with CodeConflict, and a sandbox class that
// cannot resume returns ErrNotSupportedForClass. A paused macOS sandbox whose Mac
// reservation is ending returns ErrNoActiveReservation (409): its memory is on
// that Mac, so another reservation cannot take it over. The sandbox is stopped
// with its disk when the reservation ends, and Start can then start it under
// another reservation. A suspended organization returns ErrOrgSuspended, a
// project at its monthly budget ErrProjectBudgetExceeded, and a resume the
// platform cannot take now ErrServiceUnavailable.
func (s *Sandbox) Resume(ctx context.Context, opts *LifecycleWaitOptions) error {
	return s.transition(ctx, lifecycleTransition{
		action: "resume", target: SandboxStateStarted, failure: SandboxStateStopped,
	}, opts)
}

// Delete requests deletion and waits for destroyed or a subsequent 404.
// Timeout defaults to 60 seconds; NoWait returns after acceptance. A failed
// completion returns ErrRequestTimeout or *SandboxTerminalStateError.
//
// The platform releases the sandbox's image and volumes a few seconds after it
// is destroyed: deleting them right away can return ErrConflict for a short
// while, so retry such a delete after a moment.
func (s *Sandbox) Delete(ctx context.Context, opts *LifecycleWaitOptions) error {
	return s.transition(ctx, lifecycleTransition{target: SandboxStateDestroyed}, opts)
}

type lifecycleTransition struct {
	action  string
	target  SandboxState
	failure SandboxState
}

func (s *Sandbox) transition(ctx context.Context, transition lifecycleTransition, opts *LifecycleWaitOptions) error {
	budget, err := lifecycleOptions(opts)
	if err != nil {
		return err
	}
	w, err := s.newWait([]SandboxState{transition.target})
	if err != nil {
		return err
	}
	if transition.failure != "" {
		w.terminal = append(w.terminal, transition.failure)
	}
	request := apiRequest{Method: http.MethodPost, Path: w.path + "/" + transition.action}
	if transition.action == "" {
		request.Method, request.Path = http.MethodDelete, w.path
		w.missingIsDestroyed = true
	}
	if err = s.client.api().do(ctx, request); err != nil {
		return err
	}
	if budget == NoWait {
		return nil
	}
	w.deadline, w.contextDeadline = waitDeadline(ctx, budget)
	return w.run(ctx)
}

// Start starts or resumes the sandbox identified by id. See Sandbox.Start.
func (c *Client) Start(ctx context.Context, id string, opts *LifecycleWaitOptions) error {
	s, err := c.lifecycleSandbox(id)
	if err != nil {
		return err
	}
	return s.Start(ctx, opts)
}

// Stop stops the sandbox identified by id, waiting up to 60 seconds by default.
func (c *Client) Stop(ctx context.Context, id string, opts *LifecycleWaitOptions) error {
	s, err := c.lifecycleSandbox(id)
	if err != nil {
		return err
	}
	return s.Stop(ctx, opts)
}

// Pause pauses the sandbox identified by id, waiting up to 60 seconds by default.
func (c *Client) Pause(ctx context.Context, id string, opts *LifecycleWaitOptions) error {
	s, err := c.lifecycleSandbox(id)
	if err != nil {
		return err
	}
	return s.Pause(ctx, opts)
}

// Resume resumes the sandbox identified by id, waiting up to 60 seconds by default.
func (c *Client) Resume(ctx context.Context, id string, opts *LifecycleWaitOptions) error {
	s, err := c.lifecycleSandbox(id)
	if err != nil {
		return err
	}
	return s.Resume(ctx, opts)
}

// Delete deletes the sandbox identified by id. See Sandbox.Delete for completion semantics.
func (c *Client) Delete(ctx context.Context, id string, opts *LifecycleWaitOptions) error {
	s, err := c.lifecycleSandbox(id)
	if err != nil {
		return err
	}
	return s.Delete(ctx, opts)
}

func (c *Client) lifecycleSandbox(id string) (*Sandbox, error) {
	if err := c.ensureOpen(); err != nil {
		return nil, err
	}
	if _, err := pathSegment(id); err != nil {
		return nil, err
	}
	return c.newSandbox(sandboxInit{Info: SandboxInfo{ID: id, Region: regionOf(id)}})
}
