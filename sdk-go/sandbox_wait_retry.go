// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"net/http"
	"time"
)

// waitReadRetry keeps the retry schedule and last read error across the events
// phase, a lost subscription, and the final reserve. A successful nonterminal
// read resets the backoff but does not erase the last failure's timeout cause.
type waitReadRetry struct {
	enabled  bool
	fallback bool
	lastErr  error
	delay    time.Duration
	nextRead time.Time
}

func newWaitReadRetry(source sandboxStateSource) waitReadRetry {
	// A nil subscription can mean either events are disabled or the installed
	// hub is temporarily unavailable. Only the former uses direct-poll policy.
	switch typed := source.(type) {
	case noopStateSource:
		return waitReadRetry{}
	case *eventsStateSource:
		return waitReadRetry{enabled: typed.enabled()}
	default:
		// Injected sources implement the watch contract without hub configuration.
		return waitReadRetry{enabled: true}
	}
}

func (r *waitReadRetry) missed(err error) bool {
	// A request's timeout can precede the wait deadline. The caller checks the
	// actual wait/context deadline before classifying that read as retryable.
	if !r.enabled || (waitReadDecisive(err) && !errors.Is(err, ErrClientTimeout)) {
		return false
	}
	// Like the reference hub, fallback rejects every 4xx except rate limits.
	// Early probes keep listening through other 4xx, including 400 and 408.
	if r.fallback && waitPermanentHTTPError(err) {
		return false
	}
	r.lastErr = err
	r.delay = min(max(waitMinInterval, r.delay*2), waitReadSlack)
	delay := r.delay
	var detail *Error
	if errors.As(err, &detail) {
		delay = max(delay, detail.RetryAfter)
	}
	r.nextRead = time.Now().Add(delay)
	return true
}

func waitPermanentHTTPError(err error) bool {
	var detail *Error
	return errors.As(err, &detail) && detail.StatusCode >= 400 &&
		detail.StatusCode < 500 && detail.StatusCode != http.StatusTooManyRequests
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
