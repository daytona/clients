// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

// Package wait implements wait-by-default: poll a status
// getter until a predicate holds, bounded by a timeout that cancels nothing.
package wait

import (
	"context"
	"errors"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
)

// Until polls get every interval until done(status) is true or the timeout
// elapses. The getter may itself long-poll (server ?wait=) — the interval
// then only paces retries. Returns the last status.
func Until[T any](ctx context.Context, timeout, interval time.Duration, get func(ctx context.Context, remaining time.Duration) (T, error), done func(T) (bool, error), progress func(T)) (T, error) {
	return UntilBackoff(ctx, timeout, []time.Duration{interval}, get, done, progress)
}

// UntilBackoff is Until with an interval SCHEDULE: the n-th sleep uses
// schedule[n] and the last entry repeats — for routes with no server-side
// long-poll, where a minutes-long operation should not be polled at a
// sub-second cadence for its whole life (e.g. 2s, 5s, 10s, 10s, …).
func UntilBackoff[T any](ctx context.Context, timeout time.Duration, schedule []time.Duration, get func(ctx context.Context, remaining time.Duration) (T, error), done func(T) (bool, error), progress func(T)) (T, error) {
	var zero T
	if len(schedule) == 0 {
		schedule = []time.Duration{time.Second}
	}
	attempt := 0
	deadline := time.Now().Add(timeout)
	for {
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return zero, timeoutError(timeout)
		}
		getCtx, cancel := context.WithTimeout(ctx, remaining)
		s, err := get(getCtx, remaining)
		getterTimedOut := errors.Is(getCtx.Err(), context.DeadlineExceeded) && ctx.Err() == nil
		cancel()
		if getterTimedOut {
			return zero, timeoutError(timeout)
		}
		if err != nil {
			return zero, err
		}
		if time.Until(deadline) <= 0 {
			return s, timeoutError(timeout)
		}
		ok, err := done(s)
		if err != nil {
			return s, err
		}
		if ok {
			return s, nil
		}
		if progress != nil {
			progress(s)
		}
		remaining = time.Until(deadline)
		if remaining <= 0 {
			return s, timeoutError(timeout)
		}
		sleep := schedule[len(schedule)-1]
		if attempt < len(schedule) {
			sleep = schedule[attempt]
		}
		attempt++
		if sleep > remaining {
			sleep = remaining
		}
		timer := time.NewTimer(sleep)
		select {
		case <-ctx.Done():
			timer.Stop()
			return s, cerr.New(cerr.ExitInterrupted, "interrupted")
		case <-timer.C:
		}
	}
}

func timeoutError(timeout time.Duration) error {
	return cerr.New(cerr.ExitTimeout, "timed out after %s waiting for the operation to finish", timeout).
		WithHint("the server keeps working; check on it with the resource's `get` command, or wait longer with a larger --timeout")
}
