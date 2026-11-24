// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"strings"
	"time"
)

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

// checkDuration validates a caller-supplied duration option named name: zero
// (the option's default) and positive values pass, and so does each sentinel
// in allowed (for example NoWait, WaitForever or NoTimeout, where the option
// documents it). Any other negative value is a BAD_REQUEST error from the SDK.
func checkDuration(name string, value time.Duration, allowed ...time.Duration) error {
	if value >= 0 {
		return nil
	}
	for _, sentinel := range allowed {
		if value == sentinel {
			return nil
		}
	}
	return badRequest(name + " must not be negative (" + value.String() + ")" + sentinelHint(allowed))
}

var sentinelNames = map[time.Duration]string{
	NoWait:      "NoWait",
	WaitForever: "WaitForever",
	NoTimeout:   "NoTimeout",
}

func sentinelHint(allowed []time.Duration) string {
	if len(allowed) == 0 {
		return ""
	}
	names := make([]string, 0, len(allowed))
	for _, sentinel := range allowed {
		name, ok := sentinelNames[sentinel]
		if !ok {
			name = sentinel.String()
		}
		names = append(names, name)
	}
	return "; use zero for the default, a positive duration, or " + strings.Join(names, ", ")
}
