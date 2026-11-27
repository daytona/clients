// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

// Process waits are a series of bounded rounds, never one request held for the
// whole caller budget, as in the Python and TypeScript SDKs.

func truncatedJSON(t *testing.T) http.HandlerFunc {
	t.Helper()
	return func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Content-Length", "50")
		if _, err := w.Write([]byte("{")); err != nil {
			t.Error(err)
		}
	}
}

// sequence answers each request with the next handler, the last one repeating.
func sequence(handlers ...http.HandlerFunc) http.HandlerFunc {
	var mu sync.Mutex
	calls := 0
	return func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		handler := handlers[min(calls, len(handlers)-1)]
		calls++
		mu.Unlock()
		handler(w, r)
	}
}

func TestProcessWaitRoundIsThirtySeconds(t *testing.T) {
	require.Equal(t, 30*time.Second, processWaitRound)
	// No deadline: one round, its read bounded by the hold plus slack.
	milliseconds, requestTimeout := processWaitBudget(time.Time{})
	require.Equal(t, int64(30000), milliseconds)
	require.Equal(t, 35*time.Second, requestTimeout)
	// A long budget holds one round, not the whole budget.
	milliseconds, requestTimeout = processWaitBudget(time.Now().Add(10 * time.Minute))
	require.Equal(t, int64(30000), milliseconds)
	require.Equal(t, 35*time.Second, requestTimeout)
	// The last round holds only what is left of the budget.
	milliseconds, requestTimeout = processWaitBudget(time.Now().Add(10 * time.Second))
	require.LessOrEqual(t, milliseconds, int64(10000))
	require.Greater(t, milliseconds, int64(9000))
	require.Equal(t, time.Duration(milliseconds)*time.Millisecond+waitReadSlack, requestTimeout)
}

func TestExecWithALongBudgetWaitsInBoundedRounds(t *testing.T) {
	// Given a command that outlives the start request's hold and one round.
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
		"POST /processes": jsonHandler(t, processFixture("p")),
		"GET /processes/p/wait": sequence(
			jsonHandler(t, map[string]string{"reason": "timed_out"}),
			jsonHandler(t, map[string]any{"reason": "exited", "exitCode": 0}),
		),
		"GET /processes/p":      jsonHandler(t, processFixture("p")),
		"GET /processes/p/logs": jsonHandler(t, processLogsFixture(processFrameFixture("stdout", "done"))),
	})
	// When the caller allows ten minutes.
	result, err := sandbox.Exec(t.Context(), "sleep 70; printf done", &ExecOptions{Timeout: 10 * time.Minute})
	// Then every request holds one 30 s round at most.
	require.NoError(t, err)
	require.Equal(t, "done", result.Stdout)
	requests := fake.requests()
	require.Equal(t, "30000", requests[0].Query.Get("waitMs"))
	waits := 0
	for _, request := range requests {
		if request.Path == "/processes/p/wait" {
			waits++
			require.Equal(t, "30000", request.Query.Get("timeoutMs"))
		}
	}
	require.Equal(t, 2, waits)
}

func TestUnlimitedWaitIsASeriesOfBoundedRounds(t *testing.T) {
	for name, opts := range map[string]*WaitProcessOptions{
		"nil": nil, "WaitForever": {Timeout: WaitForever}, "NoTimeout": {Timeout: NoTimeout},
		"above the timer maximum": {Timeout: maxTimerDuration + time.Second},
	} {
		t.Run(name, func(t *testing.T) {
			sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
				"GET /processes/p/wait": sequence(
					jsonHandler(t, map[string]string{"reason": "timed_out"}),
					jsonHandler(t, map[string]string{"reason": "timed_out"}),
					jsonHandler(t, map[string]any{"reason": "exited", "exitCode": 0}),
				),
				"GET /processes/p": jsonHandler(t, processFixture("p")),
			})
			handle, err := sandbox.Process().Connect("p")
			require.NoError(t, err)
			result, err := handle.Wait(t.Context(), opts)
			require.NoError(t, err)
			require.Equal(t, 0, *result.ExitCode)
			waits := 0
			for _, request := range fake.requests() {
				if request.Path == "/processes/p/wait" {
					waits++
					require.Equal(t, "30000", request.Query.Get("timeoutMs"))
				}
			}
			require.Equal(t, 3, waits)
		})
	}
}

func TestWaitRetriesARoundTheNetworkDropped(t *testing.T) {
	// Given an intermediary that cuts one quiet round short.
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
		"GET /processes/p/wait": sequence(truncatedJSON(t), jsonHandler(t, map[string]any{"reason": "exited", "exitCode": 0})),
	})
	handle, err := sandbox.Process().Connect("p")
	require.NoError(t, err)
	// When waiting without a deadline.
	result, err := handle.Wait(t.Context(), nil)
	// Then the dropped round costs one retry, not the wait.
	require.NoError(t, err)
	require.Equal(t, 0, *result.ExitCode)
	require.Len(t, fake.requests(), 2)
}

func TestExecNeverRetriesTheStartRequest(t *testing.T) {
	// Given a start request cut short: the process may already run.
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{"POST /processes": truncatedJSON(t)})
	// When running with a long budget.
	_, err := sandbox.Exec(t.Context(), "make deploy", &ExecOptions{Timeout: 10 * time.Minute})
	// Then the failure is returned as is, after one bounded request.
	require.ErrorIs(t, err, ErrConnection)
	require.Len(t, fake.requests(), 1)
	require.Equal(t, "30000", fake.requests()[0].Query.Get("waitMs"))
}

func TestSessionRunBoundsItsStartRequest(t *testing.T) {
	fixture := processFixture("command")
	fixture["state"], fixture["exitCode"], fixture["reason"] = "terminal", 0, "exited"
	fixture["logs"] = processLogsFixture(processFrameFixture("stdout", "ok"))
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{"POST /processes": jsonHandler(t, fixture)})
	session, err := sandbox.Process().ConnectSession("shell")
	require.NoError(t, err)
	_, err = session.Run(t.Context(), "true", &SessionRunOptions{Timeout: WaitForever})
	require.NoError(t, err)
	require.Equal(t, "30000", fake.requests()[0].Query.Get("waitMs"))
}
