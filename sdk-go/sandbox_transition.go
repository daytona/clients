// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"time"
)

// retrySandboxTransition retries only a definitive transition refusal, never a
// transport failure or an ordinary conflict. The request's own timeout and the
// caller's context still bound each attempt; the retry window bounds admission
// of further attempts, not an already admitted request.
func (c *Client) retrySandboxTransition(ctx context.Context, request apiRequest) (bool, error) {
	started := time.Now()
	retried := false
	for {
		err := c.api().do(ctx, request)
		var failure *Error
		if !errors.As(err, &failure) || failure.Code != CodeSandboxTransitionInProgress {
			return retried, err
		}
		delay := time.Second
		if seconds := failure.Details.RetryAfterSeconds; seconds != nil {
			delay = time.Duration(max(int64(1), min(int64(5), *seconds))) * time.Second
		}
		// A retry the window cannot admit after the full pause is not worth
		// waiting for: the refusal returns at once.
		if 30*time.Second-time.Since(started) <= delay {
			return retried, err
		}
		if pauseErr := artifactPause(ctx, c, delay); pauseErr != nil {
			return retried, pauseErr
		}
		retried = true
	}
}
