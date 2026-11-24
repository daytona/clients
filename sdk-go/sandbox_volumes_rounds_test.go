// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

// pullRoundsFake is a sandbox that holds each pull round for its waitMs on a
// fake clock (when held) and answers the next status; the last one repeats.
type pullRoundsFake struct {
	mu     sync.Mutex
	clock  time.Time
	waits  []int64
	pauses []time.Duration
}

func (f *pullRoundsFake) now() time.Time {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.clock
}

func (f *pullRoundsFake) pause(_ context.Context, delay time.Duration) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.pauses = append(f.pauses, delay)
	f.clock = f.clock.Add(delay)
	return nil
}

func newPullRounds(t *testing.T, statuses []string, held bool) (*pullRoundsFake, pullVolumeRounds) {
	t.Helper()
	fake := &pullRoundsFake{clock: time.Unix(1000, 0)}
	server := newFakeServer(t, map[string]http.HandlerFunc{"POST /volumes/v/pull": func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			RequestID string `json:"requestId"`
			WaitMs    int64  `json:"waitMs"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.RequestID != "r" {
			t.Errorf("unexpected body %+v: %v", body, err)
		}
		fake.mu.Lock()
		fake.waits = append(fake.waits, body.WaitMs)
		if held {
			fake.clock = fake.clock.Add(time.Duration(body.WaitMs) * time.Millisecond)
		}
		status := statuses[min(len(fake.waits), len(statuses))-1]
		fake.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		if status == "pending" {
			w.WriteHeader(http.StatusAccepted)
			writeJSON(t, w, map[string]any{"requestId": "r", "status": status, "manifestId": nil})
			return
		}
		writeJSON(t, w, map[string]any{"requestId": "r", "status": status, "manifestId": "m"})
	}})
	sandbox := dockerVolumeSandbox(t, server)
	return fake, pullVolumeRounds{volumes: sandbox.Volumes(), now: fake.now, pause: fake.pause}
}

func TestSandboxVolumesPullRounds(t *testing.T) {
	t.Run("waits a long pull out in rounds until a result", func(t *testing.T) {
		fake, rounds := newPullRounds(t, []string{"pending", "pending", "pulled"}, true)
		result, err := rounds.pull(t.Context(), "v", "r", 2*time.Minute)
		require.NoError(t, err)
		require.Equal(t, PullVolumeStatusPulled, result.Status)
		require.Equal(t, []int64{30000, 30000, 30000}, fake.waits)
		require.Empty(t, fake.pauses)
	})
	t.Run("ends pending at the caller deadline", func(t *testing.T) {
		fake, rounds := newPullRounds(t, []string{"pending"}, true)
		result, err := rounds.pull(t.Context(), "v", "r", 70*time.Second)
		require.NoError(t, err)
		require.Equal(t, PullVolumeStatusPending, result.Status)
		// The last round never outlives the budget.
		require.Equal(t, []int64{30000, 30000, 10000}, fake.waits)
	})
	t.Run("keeps waiting in rounds with no deadline", func(t *testing.T) {
		for _, timeout := range []time.Duration{WaitForever, maxTimerDuration + time.Second} {
			fake, rounds := newPullRounds(t, []string{"pending", "pending", "pending", "pending", "unchanged"}, true)
			result, err := rounds.pull(t.Context(), "v", "r", timeout)
			require.NoError(t, err)
			require.Equal(t, PullVolumeStatusUnchanged, result.Status)
			require.Equal(t, []int64{30000, 30000, 30000, 30000, 30000}, fake.waits)
		}
	})
	t.Run("paces rounds a sandbox answers early", func(t *testing.T) {
		fake, rounds := newPullRounds(t, []string{"pending", "pending", "pulled"}, false)
		result, err := rounds.pull(t.Context(), "v", "r", time.Minute)
		require.NoError(t, err)
		require.Equal(t, PullVolumeStatusPulled, result.Status)
		require.Len(t, fake.waits, 3)
		require.Equal(t, []time.Duration{time.Second, time.Second}, fake.pauses)
	})
}

func TestSandboxVolumesPull_whenWaitExceedsOneRound(t *testing.T) {
	// Given a sandbox that answers pulled at once.
	server := newFakeServer(t, map[string]http.HandlerFunc{"POST /volumes/v/pull": jsonHandler(t, map[string]any{
		"requestId": "r", "status": "pulled", "manifestId": "m",
	})})
	sandbox := dockerVolumeSandbox(t, server)
	// When the caller waits longer than one round.
	result, err := sandbox.Volumes().Pull(t.Context(), "v", "r", &PullVolumeOptions{Timeout: 5 * time.Minute})
	// Then the first request holds one round, and a result ends the wait.
	require.NoError(t, err)
	require.Equal(t, PullVolumeStatusPulled, result.Status)
	require.Len(t, server.requests(), 1)
	require.JSONEq(t, `{"requestId":"r","waitMs":30000}`, string(server.requests()[0].Body))
}

func TestSandboxVolumesPull_whenARoundFails(t *testing.T) {
	// Given a pending round followed by a failure.
	var calls atomic.Int32
	server := newFakeServer(t, map[string]http.HandlerFunc{"POST /volumes/v/pull": func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if calls.Add(1) == 1 {
			w.WriteHeader(http.StatusAccepted)
			writeJSON(t, w, map[string]any{"requestId": "r", "status": "pending", "manifestId": nil})
			return
		}
		w.WriteHeader(http.StatusServiceUnavailable)
		writeJSON(t, w, map[string]any{"code": "SERVICE_UNAVAILABLE", "message": "busy", "source": "DAYTONA_DAEMON"})
	}})
	sandbox := dockerVolumeSandbox(t, server)
	clock := time.Unix(0, 0)
	rounds := pullVolumeRounds{
		volumes: sandbox.Volumes(),
		now:     func() time.Time { clock = clock.Add(30 * time.Second); return clock },
		pause:   func(context.Context, time.Duration) error { return nil },
	}
	// When waiting in rounds.
	_, err := rounds.pull(t.Context(), "v", "r", 5*time.Minute)
	// Then the error is returned, never retried.
	require.ErrorIs(t, err, ErrServiceUnavailable)
	require.Len(t, server.requests(), 2)
}
