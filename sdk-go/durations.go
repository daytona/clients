// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

const (
	// NoWait returns after the server accepts an operation, without waiting for its outcome.
	NoWait time.Duration = -1
	// WaitForever removes the SDK wait deadline; caller cancellation still applies.
	WaitForever time.Duration = -2
	// NoTimeout disables a request or transfer-idle timeout; caller cancellation still applies.
	NoTimeout time.Duration = -3
	// NoLimit disables a buffered response size cap. Zero selects the default cap.
	NoLimit int64 = -1
)

const (
	defaultRequestTimeout         = 60 * time.Second
	defaultMaxResponseBytes int64 = 1 << 30
	maxTimerDuration              = time.Duration(2147483647) * time.Millisecond
	maxSafeInteger                = 9007199254740991
)

func boundedTimeout(value time.Duration) time.Duration {
	if value == NoTimeout || value == WaitForever || value > maxTimerDuration {
		return 0
	}
	return value
}

// wireSeconds rounds positive hold budgets up, without overflowing Duration.
func wireSeconds(value time.Duration) int64 {
	if value <= 0 {
		return 0
	}
	seconds := int64(value / time.Second)
	if value%time.Second != 0 {
		seconds++
	}
	return seconds
}

// wireMilliseconds preserves positive sub-millisecond budgets as one millisecond.
func wireMilliseconds(value time.Duration) int64 {
	if value <= 0 {
		return 0
	}
	milliseconds := int64(value / time.Millisecond)
	if value%time.Millisecond != 0 {
		milliseconds++
	}
	return milliseconds
}
