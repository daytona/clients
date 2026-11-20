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

// GetArtifactOptions configures an image or snapshot lookup.
type GetArtifactOptions struct {
	// Wait requests readiness. False performs an ordinary read.
	Wait bool
	// Timeout is the total readiness budget. Zero performs one server-default
	// hold, returning its observation. Positive budgets repeat holds until ready,
	// failed or error; WaitForever repeats without an SDK deadline.
	// Values above 2,147,483.647 seconds also mean forever.
	// Without Wait this field is validated but unused.
	Timeout time.Duration
}

type artifact interface{ artifactStatus() string }

func (i *Image) artifactStatus() string    { return i.RawStatus }
func (s *Snapshot) artifactStatus() string { return s.RawStatus }

type artifactReader[T artifact] struct {
	client *Client
	path   string
	parse  func(JSONValue) (T, error)
}

func (r artifactReader[T]) get(ctx context.Context, opts *GetArtifactOptions) (T, error) {
	var zero T
	options := GetArtifactOptions{}
	if opts != nil {
		options = *opts
	}
	if err := imageWaitDuration(options.Timeout); err != nil {
		return zero, err
	}
	if !options.Wait {
		return r.read(ctx, apiRequest{})
	}
	if options.Timeout == 0 {
		return r.read(ctx, apiRequest{Query: url.Values{"wait": {"ready"}}, Timeout: NoTimeout})
	}
	waitCtx, cancel := artifactWaitContext(ctx, options.Timeout, "artifact did not become ready before timeout")
	defer cancel()
	return r.wait(waitCtx)
}

func (r artifactReader[T]) read(ctx context.Context, request apiRequest) (T, error) {
	var zero T
	var raw JSONValue
	request.Path, request.Result = r.path, &raw
	if err := r.client.api().do(ctx, request); err != nil {
		return zero, err
	}
	return r.parse(raw)
}

func (r artifactReader[T]) wait(ctx context.Context) (T, error) {
	var zero T
	for {
		if err := ctx.Err(); err != nil {
			return zero, artifactWaitError(ctx, transportError(ctx, err))
		}
		started := time.Now()
		hold, timeout := artifactHold(ctx)
		value, err := r.read(ctx, apiRequest{
			Query:   url.Values{"wait": {"ready"}, "timeout_ms": {strconv.FormatInt(wireMilliseconds(hold), 10)}},
			Timeout: timeout,
		})
		if err != nil {
			return zero, artifactWaitError(ctx, err)
		}
		switch value.artifactStatus() {
		case "ready", "failed", "error":
			return value, nil
		default:
			if err = artifactPause(ctx, r.client, time.Second-time.Since(started)); err != nil {
				return zero, artifactWaitError(ctx, err)
			}
		}
	}
}

func imageWaitDuration(value time.Duration) error {
	if value < 0 && value != WaitForever {
		return badRequest("timeout must be non-negative or WaitForever")
	}
	return nil
}

func artifactWaitContext(
	ctx context.Context, timeout time.Duration, message string,
) (context.Context, context.CancelFunc) {
	if bound := boundedTimeout(timeout); bound > 0 {
		failure := newSDKError(CodeRequestTimeout, 408, message)
		failure.Err = context.DeadlineExceeded
		return context.WithTimeoutCause(ctx, bound, failure)
	}
	return context.WithCancel(ctx)
}

func artifactWaitError(ctx context.Context, err error) error {
	var sdkError *Error
	if errors.As(context.Cause(ctx), &sdkError) {
		return sdkError
	}
	return err
}

func artifactHold(ctx context.Context) (time.Duration, time.Duration) {
	hold := 60 * time.Second
	if deadline, ok := ctx.Deadline(); ok {
		hold = min(hold, max(time.Until(deadline), time.Nanosecond))
	}
	return hold, hold + 5*time.Second
}

func artifactPause(ctx context.Context, client *Client, pause time.Duration) error {
	timer := time.NewTimer(max(0, pause))
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return transportError(ctx, ctx.Err())
	case <-client.ctx.Done():
		return closedError()
	case <-timer.C:
		return nil
	}
}
