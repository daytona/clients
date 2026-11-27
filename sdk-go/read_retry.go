// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"net/http"
	"time"
)

// Waits (sandbox state, Docker status, image and snapshot readiness) read the
// server until a target or terminal state within one deadline. A read that
// fails transiently (no response, the client's own read timer, HTTP 429 other
// than LIMIT_EXCEEDED, a 5xx other than 501) is retried after a backoff of 200 ms doubling to 5 s, or the
// server's RetryAfter when longer, never past the wait's deadline. A spent
// deadline returns the wait's own timeout with the last failed read as its
// cause. Any other failure (a 4xx, a 501, LIMIT_EXCEEDED) returns at once.
// Only reads are retried, never a request that changes something. The Python
// and TypeScript SDKs apply the same policy.

// readRetryMaxBackoff caps the doubling backoff between failed reads.
const readRetryMaxBackoff = 5 * time.Second

// readRetryable reports whether a failed observation read may be retried. It
// inspects the outermost *Error only: a wait timeout wrapping a client timeout
// is not itself retryable.
func readRetryable(err error) bool {
	var detail *Error
	if !errors.As(err, &detail) {
		return false
	}
	if detail.Is(ErrClientTimeout) || detail.Is(ErrConnection) {
		return true
	}
	if detail.StatusCode == http.StatusTooManyRequests {
		return detail.Code != CodeLimitExceeded
	}
	return detail.StatusCode >= 500 && detail.StatusCode < 600 && detail.StatusCode != http.StatusNotImplemented
}

// readRetryDelay returns the next backoff after previous and the pause before
// the next read, which honors the server's RetryAfter.
func readRetryDelay(previous time.Duration, err error) (backoff, pause time.Duration) {
	backoff = min(max(waitMinInterval, previous*2), readRetryMaxBackoff)
	pause = backoff
	var detail *Error
	if errors.As(err, &detail) {
		pause = max(pause, detail.RetryAfter)
	}
	return backoff, pause
}

// readBackoff is the retry state of one Docker or artifact wait.
type readBackoff struct {
	lastErr error
	delay   time.Duration
}

// next records a failed read and returns the pause before the next one, or
// false when err must be returned instead. A transient failure is kept as the
// timeout's cause even when the context has ended meanwhile.
func (b *readBackoff) next(ctx context.Context, err error) (time.Duration, bool) {
	if !readRetryable(err) {
		return 0, false
	}
	b.lastErr = err
	if ctx.Err() != nil {
		return 0, false
	}
	var pause time.Duration
	b.delay, pause = readRetryDelay(b.delay, err)
	return pause, true
}

func (b *readBackoff) succeeded() { b.delay = 0 }
