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
// Caller cancellation/deadlines and Client.Close interrupt both polls and pauses.
func (d *DockerService) WaitUntilReady(ctx context.Context, opts *DockerWaitOptions) (DockerStatus, error) {
	budget := time.Minute
	if opts != nil && opts.Timeout != 0 {
		budget = opts.Timeout
	}
	if budget < 0 && budget != WaitForever {
		return DockerStatus{}, badRequest("Docker wait timeout must be positive or WaitForever")
	}
	var deadline time.Time
	if duration := boundedTimeout(budget); duration > 0 {
		deadline = time.Now().Add(duration)
	}
	return d.waitReady(ctx, deadline)
}

func (d *DockerService) waitReady(ctx context.Context, deadline time.Time) (DockerStatus, error) {
	var last *DockerStatus
	for {
		if err := ctx.Err(); err != nil {
			return DockerStatus{}, transportError(ctx, err)
		}
		if err := d.sandbox.client.ensureOpen(); err != nil {
			return DockerStatus{}, err
		}
		pollStarted := time.Now()
		status, err := d.poll(ctx, deadline, last)
		if err != nil {
			return DockerStatus{}, err
		}
		last = &status
		switch status.State {
		case DockerStateReady:
			return status, nil
		case DockerStateFailed, DockerStateNotPresent, DockerStateDisabled,
			DockerStateUnmanaged, DockerStateUnsupported:
			message := "docker is " + status.RawState
			if status.Reason != "" {
				message += ": " + status.Reason
			}
			return DockerStatus{}, &DockerNotReadyError{SDKError: newSDKError("CONFLICT", 409, message), Status: status}
		case DockerStateStarting, DockerStateDeferred, DockerStateUnknown:
			// Unknown future states remain eligible for polling.
		}
		pause := 500*time.Millisecond - time.Since(pollStarted)
		if !deadline.IsZero() {
			pause = min(pause, time.Until(deadline))
		}
		timer := time.NewTimer(max(0, pause))
		select {
		case <-ctx.Done():
			timer.Stop()
			return DockerStatus{}, transportError(ctx, ctx.Err())
		case <-d.sandbox.client.ctx.Done():
			timer.Stop()
			return DockerStatus{}, closedError()
		case <-timer.C:
		}
	}
}

func (d *DockerService) poll(ctx context.Context, deadline time.Time, last *DockerStatus) (DockerStatus, error) {
	hold, timeout := dockerPollBudget(deadline, time.Now())
	if hold <= 0 {
		return DockerStatus{}, dockerWaitTimeout(last, nil)
	}
	status, err := d.readStatus(ctx, toolboxRequest{
		Path: "/docker/status", Query: url.Values{"waitMs": {strconv.FormatInt(wireMilliseconds(hold), 10)}},
		Timeout: timeout,
	})
	if ctx.Err() == nil && errors.Is(err, ErrClientTimeout) &&
		!deadline.IsZero() && !time.Now().Before(deadline) {
		return DockerStatus{}, dockerWaitTimeout(last, err)
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
