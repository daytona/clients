// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package wait

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
)

func TestUntil(t *testing.T) {
	n := 0
	s, err := Until(context.Background(), time.Second, time.Millisecond, func(context.Context, time.Duration) (string, error) {
		n++
		if n < 3 {
			return "pending", nil
		}
		return "running", nil
	}, func(s string) (bool, error) { return s == "running", nil }, nil)
	if err != nil || s != "running" || n != 3 {
		t.Fatal(s, err, n)
	}
}

func TestTimeout(t *testing.T) {
	_, err := Until(context.Background(), 5*time.Millisecond, time.Millisecond, func(context.Context, time.Duration) (string, error) { return "pending", nil }, func(string) (bool, error) { return false, nil }, nil)
	if cerr.ExitCode(err) != cerr.ExitTimeout {
		t.Fatal(err)
	}
	// Commands such as `sandbox wait` have no --no-wait: the hint must apply
	// to every command that waits.
	if hint := cerr.DetailsOf(err).Hint; strings.Contains(hint, "--no-wait") || !strings.Contains(hint, "--timeout") {
		t.Fatalf("hint %q", hint)
	}
}

func TestTerminalError(t *testing.T) {
	_, err := Until(context.Background(), time.Second, time.Millisecond, func(context.Context, time.Duration) (string, error) { return "failed", nil },
		func(s string) (bool, error) {
			if s == "failed" {
				return false, cerr.New(cerr.ExitFailure, "sandbox failed")
			}
			return false, nil
		}, nil)
	if err == nil || cerr.ExitCode(err) != cerr.ExitFailure {
		t.Fatal(err)
	}
}

func TestUntil_passesRemainingDeadlineToGetter(t *testing.T) {
	var remaining time.Duration
	_, err := Until(context.Background(), 50*time.Millisecond, time.Millisecond, func(_ context.Context, left time.Duration) (string, error) {
		remaining = left
		return "done", nil
	}, func(string) (bool, error) { return true, nil }, nil)
	if err != nil {
		t.Fatal(err)
	}
	if remaining <= 0 || remaining > 50*time.Millisecond {
		t.Fatalf("remaining=%s", remaining)
	}
}

// UntilBackoff sleeps the schedule in order and repeats the last entry.
func TestUntilBackoff_followsScheduleThenRepeatsLast(t *testing.T) {
	var sleeps []time.Duration
	prev := time.Now()
	calls := 0
	_, err := UntilBackoff(context.Background(), 5*time.Second, []time.Duration{10 * time.Millisecond, 30 * time.Millisecond, 60 * time.Millisecond},
		func(context.Context, time.Duration) (int, error) {
			now := time.Now()
			if calls > 0 {
				sleeps = append(sleeps, now.Sub(prev))
			}
			prev = now
			calls++
			return calls, nil
		},
		func(n int) (bool, error) { return n >= 5, nil }, nil)
	if err != nil {
		t.Fatal(err)
	}
	want := []time.Duration{10 * time.Millisecond, 30 * time.Millisecond, 60 * time.Millisecond, 60 * time.Millisecond}
	if len(sleeps) != len(want) {
		t.Fatalf("sleeps %v", sleeps)
	}
	for i, w := range want {
		if sleeps[i] < w || sleeps[i] > w+200*time.Millisecond {
			t.Fatalf("sleep %d = %v, want ≈%v", i, sleeps[i], w)
		}
	}
}

func TestUntilBackoff_timesOutWhenGetterReturnsAfterBudget(t *testing.T) {
	_, err := UntilBackoff(context.Background(), 20*time.Millisecond, []time.Duration{time.Millisecond}, func(context.Context, time.Duration) (string, error) {
		time.Sleep(40 * time.Millisecond)
		return "running", nil
	}, func(string) (bool, error) {
		return true, nil
	}, nil)
	if cerr.ExitCode(err) != cerr.ExitTimeout {
		t.Fatal(err)
	}
}
