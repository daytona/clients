// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"net/url"
	"slices"
	"strconv"
	"time"
)

type waitReadOptions struct {
	deadline time.Time
	longPoll bool
}

type waitReadResult struct {
	detail sandboxObservation
	err    error
}

func (w *sandboxWait) readRequest(ctx context.Context, options waitReadOptions) (apiRequest, error) {
	remaining := waitRemaining(options.deadline)
	if remaining == 0 {
		return apiRequest{}, w.expiredAt(ctx, options.deadline)
	}
	budget := w.sandbox.client.config.timeout
	spec := apiRequest{Path: w.path}
	targets := slices.Compact(slices.Sorted(slices.Values(w.states)))
	if options.longPoll && (budget <= 0 || budget > waitSlice) {
		budget = waitSlice
	}
	if options.longPoll && len(targets) == 1 {
		target, err := waitWireState(targets[0])
		if err != nil {
			return apiRequest{}, err
		}
		hold := waitSlice
		if remaining > 0 {
			hold = min(hold, remaining)
		}
		key := "wait"
		if targets[0] == SandboxStateDestroyed {
			key = "wait_exact"
		}
		spec.Query = url.Values{key: {target}, "timeout_ms": {strconv.FormatInt(wireMilliseconds(hold), 10)}}
		budget = hold + waitReadSlack
	}
	if remaining > 0 && (budget <= 0 || remaining < budget) {
		budget = remaining
	}
	spec.Timeout = budget
	if budget <= 0 {
		spec.Timeout = NoTimeout
	}
	return spec, nil
}

func (w *sandboxWait) fetch(ctx context.Context, options waitReadOptions) waitReadResult {
	spec, err := w.readRequest(ctx, options)
	if err != nil {
		return waitReadResult{err: err}
	}
	var raw JSONValue
	spec.Result = &raw
	if err = w.sandbox.client.api().do(ctx, spec); err != nil {
		if ctx.Err() == nil && errors.Is(err, ErrClientTimeout) && waitRemaining(options.deadline) == 0 {
			return waitReadResult{err: w.expiredAt(ctx, options.deadline)}
		}
		return waitReadResult{err: err}
	}
	detail, err := parseSandbox(raw)
	return waitReadResult{detail: detail, err: err}
}

func (w *sandboxWait) accept(result waitReadResult) (stateObservation, error) {
	if result.err != nil {
		if w.missingIsDestroyed && errors.Is(result.err, ErrNotFound) {
			gone := stateObservation{State: SandboxStateDestroyed, RawState: "deleted"}
			w.sandbox.observeWait(gone)
			return gone, nil
		}
		return stateObservation{}, result.err
	}
	endpoint, err := scopedToolboxURL(result.detail.toolboxURL, w.sandbox.id, "")
	if err != nil {
		return stateObservation{}, err
	}
	s := w.sandbox
	s.mu.Lock()
	defer s.mu.Unlock()
	s.replaceInfo(result.detail.info)
	s.reasonFromRead = true
	s.info.ID = s.id
	if !s.explicitToolboxURL && endpoint != "" {
		s.toolboxURL = endpoint
	}
	return stateObservation{State: s.info.State, RawState: s.info.RawState, Reason: s.info.Error}, nil
}

func (w *sandboxWait) read(ctx context.Context, options waitReadOptions) (stateObservation, error) {
	return w.accept(w.fetch(ctx, options))
}

func (w *sandboxWait) readOutcome(outcome stateObservation) (bool, error) {
	if w.target(outcome.State) {
		return true, nil
	}
	if slices.Contains(w.terminal, outcome.State) {
		return true, w.terminalError(outcome)
	}
	return false, nil
}

// observeWait records a wait observation. Its error message replaces the
// handle's; without one, a message a full read reported for the same state and
// wire status stays (with its provenance), and any other is dropped.
func (s *Sandbox) observeWait(outcome stateObservation) {
	s.mu.Lock()
	defer s.mu.Unlock()
	keep := outcome.Reason == "" && s.reasonFromRead &&
		s.info.State == outcome.State && s.info.RawState == outcome.RawState
	s.info.State, s.info.RawState = outcome.State, outcome.RawState
	if keep {
		return
	}
	s.info.Error = outcome.Reason
	s.reasonFromRead = false
}

// Read the reason and its provenance under one lock; never carry the lock into
// the confirming network read. Public Info still includes event-backed reasons.
func (s *Sandbox) readReasonFor(outcome stateObservation) string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if s.reasonFromRead && s.info.State == outcome.State && s.info.RawState == outcome.RawState {
		return s.info.Error
	}
	return ""
}
