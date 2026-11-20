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

// Wait observes completion. Nil options wait indefinitely; a positive Timeout
// returns reason timed_out without stopping execution. Cancellation and transport
// errors return err. Non-zero exit codes are successful observations, not errors.
func (h *ProcessHandle) Wait(ctx context.Context, opts *WaitProcessOptions) (*WaitResult, error) {
	var value time.Duration
	if opts != nil {
		value = opts.Timeout
	}
	budget, err := processTimeout(value)
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
	if processWaitExpired(deadline) {
		return &WaitResult{Reason: ProcessReasonTimedOut}, true, nil
	}
	// timed_out can mean a server execution deadline, not only an expired hold.
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

func processWaitBudget(deadline time.Time) (int64, time.Duration) {
	if deadline.IsZero() {
		return 0, NoTimeout
	}
	milliseconds := max(1, wireMilliseconds(time.Until(deadline)))
	return milliseconds, time.Duration(milliseconds)*time.Millisecond + 5*time.Second
}
