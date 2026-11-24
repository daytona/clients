// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"strconv"
	"time"
)

// processWaitRound is the longest server hold of one process wait request: the
// create hold of a blocking run and every /wait round. A longer budget, or
// none, is waited out in rounds, so no request stays open (and quiet) for the
// whole wait. A /wait round (or the record read after it) that an
// intermediary drops costs one round, which Wait retries; the create request
// of a blocking run is never retried.
const processWaitRound = 30 * time.Second

// Wait observes completion. Nil options wait indefinitely; a positive Timeout
// returns reason timed_out without stopping execution. The wait is a series of
// requests that each hold for at most 30 seconds, so no single request stays
// open for the whole budget; a dropped or stalled round is retried within the
// budget.
// Cancellation and transport errors return err. Non-zero exit codes are
// successful observations, not errors.
func (h *ProcessHandle) Wait(ctx context.Context, opts *WaitProcessOptions) (*WaitResult, error) {
	var value time.Duration
	if opts != nil {
		value = opts.Timeout
	}
	budget, err := processTimeout("Timeout", value)
	if err != nil {
		return nil, err
	}
	var deadline time.Time
	if budget > 0 {
		deadline = time.Now().Add(budget)
	}
	failures := 0
	for {
		if ctx.Err() != nil {
			return nil, transportError(ctx, ctx.Err())
		}
		if processWaitExpired(deadline) {
			return &WaitResult{Reason: ProcessReasonTimedOut}, nil
		}
		result, terminal, waitErr := h.waitOnce(ctx, deadline)
		if waitErr == nil {
			failures = 0
			if terminal {
				return result, nil
			}
			continue
		}
		if ctx.Err() != nil {
			return nil, transportError(ctx, waitErr)
		}
		if !errors.Is(waitErr, ErrConnection) && !errors.Is(waitErr, ErrClientTimeout) {
			return nil, waitErr
		}
		if processWaitExpired(deadline) {
			return &WaitResult{Reason: ProcessReasonTimedOut}, nil
		}
		failures++
		if failures > 3 {
			return nil, waitErr
		}
	}
}

func (h *ProcessHandle) waitOnce(ctx context.Context, deadline time.Time) (*WaitResult, bool, error) {
	milliseconds, requestTimeout := processWaitBudget(deadline)
	var raw json.RawMessage
	if err := h.request(ctx, toolboxRequest{
		Path: "/wait", Query: url.Values{"timeoutMs": {strconv.FormatInt(milliseconds, 10)}},
		Timeout: requestTimeout, Result: &raw,
	}); err != nil {
		return nil, false, err
	}
	object, err := parseObject(raw, "wait")
	if err != nil {
		return nil, false, err
	}
	result, err := parseProcessWait(object)
	if err != nil {
		return nil, false, err
	}
	if result.Reason != ProcessReasonTimedOut {
		return result, true, nil
	}
	// timed_out can mean a server execution deadline, not only an expired
	// hold, and the record says which. It is read after the round that spends
	// the budget too, so a process that ended with that round is reported. The
	// read is bounded like a round (the slack alone once the budget is spent),
	// never by the whole budget or the client request timeout.
	_, requestTimeout = processWaitBudget(deadline)
	if err = h.request(ctx, toolboxRequest{Result: &raw, Timeout: requestTimeout}); err != nil {
		return nil, false, err
	}
	info, err := parseProcess(raw)
	if err != nil {
		return nil, false, err
	}
	return &info.WaitResult, info.State == ProcessStateTerminal, nil
}

func processWaitExpired(deadline time.Time) bool {
	return !deadline.IsZero() && time.Until(deadline) <= 0
}

// processWaitBudget returns the server hold of one wait round in wire
// milliseconds, at most processWaitRound, and the request timeout that bounds
// the round: the hold plus slack, so a stalled request ends with its round.
func processWaitBudget(deadline time.Time) (int64, time.Duration) {
	hold := processWaitRound
	if !deadline.IsZero() {
		hold = min(hold, time.Until(deadline))
	}
	milliseconds := max(1, wireMilliseconds(hold))
	return milliseconds, time.Duration(milliseconds)*time.Millisecond + waitReadSlack
}
