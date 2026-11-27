// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"time"
)

// waitReadRetry keeps the retry schedule and last read error across the events
// phase, a lost subscription, and the final reserve. A successful nonterminal
// read resets the backoff but does not erase the last failure's timeout cause.
type waitReadRetry struct {
	fallback bool
	lastErr  error
	delay    time.Duration
	nextRead time.Time
}

func (r *waitReadRetry) missed(err error) bool {
	// A request's timeout can precede the wait deadline. The caller checks the
	// actual wait/context deadline before classifying that read as retryable.
	if waitReadDecisive(err) && !errors.Is(err, ErrClientTimeout) {
		return false
	}
	// Polling retries only transient failures (readRetryable). Early probes
	// keep listening through other failures, including 400 and 408.
	if r.fallback && !readRetryable(err) {
		return false
	}
	r.lastErr = err
	var delay time.Duration
	r.delay, delay = readRetryDelay(r.delay, err)
	r.nextRead = time.Now().Add(delay)
	return true
}

func (r *waitReadRetry) remaining() time.Duration {
	if r.nextRead.IsZero() {
		return 0
	}
	return max(0, time.Until(r.nextRead))
}

func (r *waitReadRetry) succeeded() {
	r.delay = 0
	r.nextRead = time.Time{}
}

func (r *waitReadRetry) finish(err error) error {
	if r.lastErr != nil && waitSDKTimeout(err) {
		return waitTimeout(r.lastErr)
	}
	return err
}

func waitSDKTimeout(err error) bool {
	var detail *Error
	return errors.As(err, &detail) && detail.Code == CodeRequestTimeout &&
		detail.Source == SourceSDK && !errors.Is(err, ErrClientTimeout)
}

func (w *sandboxWait) retryReadError(ctx context.Context, err error, retry *waitReadRetry) error {
	if stopped := w.check(ctx); stopped != nil {
		return stopped
	}
	if retry.missed(err) {
		return nil
	}
	return err
}
