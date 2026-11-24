// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"net/url"
	"strconv"
	"time"
)

// DockerWaitOptions configures a read-only Docker readiness wait.
type DockerWaitOptions struct {
	// Timeout defaults to one minute. WaitForever (or a value above the longest
	// supported timer, about 24.8 days) removes the SDK deadline.
	// Other negative values, including NoWait and NoTimeout, return ErrBadRequest.
	Timeout time.Duration
}

// WaitUntilReady polls until Docker is ready, without starting it.
// Terminal states return *DockerNotReadyError; a spent SDK budget returns
// *DockerWaitTimeoutError, carrying the last status if one arrived.
// A status read that fails transiently (no response, a client read timeout,
// HTTP 429, or a 5xx other than 501) is retried with backoff within the
// budget; the timeout then wraps the last failure. Other failures return at once.
// Caller cancellation/deadlines and Client.Close interrupt both polls and pauses.
func (d *Docker) WaitUntilReady(ctx context.Context, opts *DockerWaitOptions) (*DockerStatus, error) {
	if err := d.sandbox.client.ensureOpen(); err != nil {
		return nil, err
	}
	budget := time.Minute
	if opts != nil && opts.Timeout != 0 {
		budget = opts.Timeout
	}
	if budget < 0 && budget != WaitForever {
		return nil, badRequest("Timeout must be positive or WaitForever")
	}
	var deadline time.Time
	if duration := boundedTimeout(budget); duration > 0 {
		deadline = time.Now().Add(duration)
	}
	return d.waitReady(ctx, deadline)
}

func (d *Docker) waitReady(ctx context.Context, deadline time.Time) (*DockerStatus, error) {
	var last *DockerStatus
	var retry readBackoff
	for {
		if err := ctx.Err(); err != nil {
			return nil, transportError(ctx, err)
		}
		if err := d.sandbox.client.ensureOpen(); err != nil {
			return nil, err
		}
		pollStarted := time.Now()
		status, err := d.poll(ctx, deadline, last, retry.lastErr)
		if err != nil {
			pause, retryable := retry.next(ctx, err)
			if !retryable {
				return nil, err
			}
			if err = d.pause(ctx, deadline, pause); err != nil {
				return nil, err
			}
			continue
		}
		retry.succeeded()
		last = status
		switch status.State {
		case DockerStateReady:
			return status, nil
		case DockerStateFailed, DockerStateNotPresent, DockerStateDisabled,
			DockerStateUnmanaged, DockerStateUnsupported:
			message := "docker is " + status.RawState
			if status.Reason != "" {
				message += ": " + status.Reason
			}
			return nil, &DockerNotReadyError{SDKError: newSDKError("CONFLICT", 409, message), Status: *status}
		case DockerStateStarting, DockerStateDeferred, DockerStateUnknown:
			// Unknown future states remain eligible for polling.
		}
		if err = d.pause(ctx, deadline, 500*time.Millisecond-time.Since(pollStarted)); err != nil {
			return nil, err
		}
	}
}

// pause sleeps up to the deadline, interrupted by the caller and Client.Close.
func (d *Docker) pause(ctx context.Context, deadline time.Time, pause time.Duration) error {
	if !deadline.IsZero() {
		pause = min(pause, time.Until(deadline))
	}
	timer := time.NewTimer(max(0, pause))
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return transportError(ctx, ctx.Err())
	case <-d.sandbox.client.ctx.Done():
		return closedError()
	case <-timer.C:
		return nil
	}
}

// poll reads the status once; a spent budget wraps lastErr, the wait's last
// failed read, when no request was made.
func (d *Docker) poll(
	ctx context.Context, deadline time.Time, last *DockerStatus, lastErr error,
) (*DockerStatus, error) {
	hold, timeout := dockerPollBudget(deadline, time.Now())
	if hold <= 0 {
		return nil, dockerWaitTimeout(last, lastErr)
	}
	status, err := d.readStatus(ctx, toolboxRequest{
		Path: "/docker/status", Query: url.Values{"waitMs": {strconv.FormatInt(wireMilliseconds(hold), 10)}},
		Timeout: timeout,
	})
	if ctx.Err() == nil && errors.Is(err, ErrClientTimeout) &&
		!deadline.IsZero() && !time.Now().Before(deadline) {
		return nil, dockerWaitTimeout(last, err)
	}
	return status, err
}

func dockerPollBudget(deadline, now time.Time) (time.Duration, time.Duration) {
	if deadline.IsZero() {
		return time.Second, 6 * time.Second
	}
	remaining := deadline.Sub(now)
	if remaining <= 0 {
		return 0, 0
	}
	remaining = time.Duration(wireMilliseconds(remaining)) * time.Millisecond
	hold := min(time.Second, remaining)
	return hold, min(hold+5*time.Second, remaining+250*time.Millisecond)
}

func dockerWaitTimeout(last *DockerStatus, cause error) *DockerWaitTimeoutError {
	message := "docker did not become ready within the requested timeout"
	if last != nil {
		message += " (last state: " + last.RawState + ")"
	}
	sdkError := newSDKError("REQUEST_TIMEOUT", 408, message)
	sdkError.Err = cause
	return &DockerWaitTimeoutError{SDKError: sdkError, Status: last}
}
