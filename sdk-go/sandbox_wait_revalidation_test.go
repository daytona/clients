// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"sync/atomic"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

// Scripted state sequences for the sandbox waits: a target event is followed by
// a read that decides like any other, and Start that joins a start or resume
// another caller sent ends when that operation fails. The Python and TypeScript
// twins are test_wait_target_revalidation.py and wait-target-revalidation.test.ts.

// scriptedReads answers each GET with the next status; the last one repeats.
func scriptedReads(t *testing.T, statuses ...string) http.HandlerFunc {
	t.Helper()
	var reads atomic.Int32
	return func(w http.ResponseWriter, _ *http.Request) {
		index := min(int(reads.Add(1)), len(statuses)) - 1
		detail := sandboxFixture("sbx")
		detail["status"] = statuses[index]
		if statuses[index] == "failed" {
			detail["error"] = "the guest crashed"
		}
		writeJSON(t, w, detail)
	}
}

func eventSource(frames ...stateObservation) *waitTestSource {
	source := &waitTestSource{events: make(chan stateObservation, len(frames))}
	for _, frame := range frames {
		source.events <- frame
	}
	return source
}

func reads(fake *fakeServer) []recordedRequest {
	var result []recordedRequest
	for _, request := range fake.requests() {
		if request.Method == http.MethodGet {
			result = append(result, request)
		}
	}
	return result
}

func posts(fake *fakeServer) []string {
	var result []string
	for _, request := range fake.requests() {
		if request.Method == http.MethodPost {
			result = append(result, request.Path)
		}
	}
	return result
}

func TestWait_whenTargetEventReadDisagrees_waitsForTarget(t *testing.T) {
	// Given: the read after the started event still shows the sandbox creating.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx": scriptedReads(t, "pending", "pending", "running"),
	})
	sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
	require.NoError(t, err)
	sandbox.client.stateSource = eventSource(stateObservation{State: SandboxStateStarted, RawState: "running"})
	// When
	err = sandbox.Wait(t.Context(), &WaitOptions{Timeout: 30 * time.Second})
	// Then: the refresh is plain; the reads after it are long polls for the target.
	require.NoError(t, err)
	require.Equal(t, SandboxStateStarted, sandbox.State())
	got := reads(fake)
	require.Len(t, got, 3)
	require.Empty(t, got[0].Query)
	for _, read := range got[1:] {
		require.Equal(t, "running", read.Query.Get("wait"))
	}
}

func TestWait_whenTargetEventReadIsTerminal_returnsTerminalError(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx": scriptedReads(t, "failed")})
	sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
	require.NoError(t, err)
	sandbox.client.stateSource = eventSource(stateObservation{State: SandboxStateStarted, RawState: "running"})
	// When
	err = sandbox.Wait(t.Context(), &WaitOptions{Timeout: 30 * time.Second})
	// Then
	var terminal *SandboxTerminalStateError
	require.ErrorAs(t, err, &terminal)
	require.Equal(t, SandboxStateError, terminal.State)
	require.Equal(t, "failed", terminal.RawState)
	require.Equal(t, "the guest crashed", terminal.Reason)
	require.Len(t, reads(fake), 1)
}

func TestWait_whenTargetEventReadNeverConverges_timesOutOnTheSameDeadline(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		began := time.Now()
		sandbox := virtualWaitSandbox(t, scriptedReads(t, "pending"))
		sandbox.client.stateSource = eventSource(stateObservation{State: SandboxStateStarted, RawState: "running"})
		// When
		err := sandbox.Wait(t.Context(), &WaitOptions{Timeout: 5 * time.Second})
		// Then: the budget is not restarted after the event.
		require.ErrorIs(t, err, ErrRequestTimeout)
		var detail *Error
		require.ErrorAs(t, err, &detail)
		require.Equal(t, SourceSDK, detail.Source)
		require.Equal(t, 5*time.Second, time.Since(began))
		require.Equal(t, SandboxStateCreating, sandbox.State())
	})
}

func TestLifecycle_whenTargetEventReadLags_waitsForTarget(t *testing.T) {
	for _, test := range []struct {
		verb     string
		statuses []string
		frame    stateObservation
		target   SandboxState
	}{
		// Start reads the sandbox first; the read after the event lags behind it.
		{"start", []string{"stopped", "booting", "running"}, stateObservation{State: SandboxStateStarted, RawState: "running"}, SandboxStateStarted},
		{"resume", []string{"booting", "running"}, stateObservation{State: SandboxStateStarted, RawState: "running"}, SandboxStateStarted},
		{"stop", []string{"stopping", "stopped"}, stateObservation{State: SandboxStateStopped, RawState: "stopped"}, SandboxStateStopped},
		{"pause", []string{"pausing", "paused"}, stateObservation{State: SandboxStatePaused, RawState: "paused"}, SandboxStatePaused},
	} {
		t.Run(test.verb, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx":               scriptedReads(t, test.statuses...),
				"POST /sandboxes/sbx/" + test.verb: jsonHandler(t, map[string]string{}),
			})
			sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
			require.NoError(t, err)
			sandbox.client.stateSource = eventSource(test.frame)
			options := &LifecycleWaitOptions{Timeout: 30 * time.Second}
			// When
			switch test.verb {
			case "start":
				err = sandbox.Start(t.Context(), options)
			case "resume":
				err = sandbox.Resume(t.Context(), options)
			case "stop":
				err = sandbox.Stop(t.Context(), options)
			case "pause":
				err = sandbox.Pause(t.Context(), options)
			}
			// Then: every scripted read was served; the lagging one did not end the wait.
			require.NoError(t, err)
			require.Equal(t, test.target, sandbox.State())
			require.Len(t, reads(fake), len(test.statuses))
		})
	}
}

func TestStart_whenJoinedStartFails_returnsTerminalError(t *testing.T) {
	for _, events := range []bool{true, false} {
		for _, failed := range []struct {
			raw   string
			state SandboxState
		}{{"stopped", SandboxStateStopped}, {"paused", SandboxStatePaused}} {
			name := failed.raw + "/polling"
			if events {
				name = failed.raw + "/events"
			}
			t.Run(name, func(t *testing.T) {
				// Given: the first read finds the sandbox starting (another
				// caller sent the start or resume); it fails and goes back.
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"GET /sandboxes/sbx": scriptedReads(t, "booting", "booting", failed.raw),
				})
				sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
				require.NoError(t, err)
				if events {
					sandbox.client.stateSource = eventSource(
						stateObservation{State: SandboxStateStarting, RawState: "booting"},
						stateObservation{State: failed.state, RawState: failed.raw},
					)
				}
				// When
				err = sandbox.Start(t.Context(), &LifecycleWaitOptions{Timeout: 30 * time.Second})
				// Then: nothing is sent; the start is joined, never repeated.
				var terminal *SandboxTerminalStateError
				require.ErrorAs(t, err, &terminal)
				require.Equal(t, failed.state, terminal.State)
				require.Equal(t, failed.raw, terminal.RawState)
				require.Empty(t, posts(fake))
			})
		}
	}
}

func TestStart_whenJoinedStartSucceeds_returns(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx": scriptedReads(t, "booting", "booting", "running"),
	})
	sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
	require.NoError(t, err)
	// When
	err = sandbox.Start(t.Context(), &LifecycleWaitOptions{Timeout: 30 * time.Second})
	// Then
	require.NoError(t, err)
	require.Equal(t, SandboxStateStarted, sandbox.State())
	require.Empty(t, posts(fake))
}

func TestStart_whenStoppedSandboxStillReadsStopped_keepsWaiting(t *testing.T) {
	// Given: the start is accepted, but the first read still shows the sandbox
	// stopped; that is no failure of the start this call sent.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx":        scriptedReads(t, "stopped", "stopped", "booting", "running"),
		"POST /sandboxes/sbx/start": jsonHandler(t, map[string]string{}),
	})
	sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
	require.NoError(t, err)
	// When
	err = sandbox.Start(t.Context(), &LifecycleWaitOptions{Timeout: 30 * time.Second})
	// Then
	require.NoError(t, err)
	require.Equal(t, SandboxStateStarted, sandbox.State())
	require.Equal(t, []string{"/sandboxes/sbx/start"}, posts(fake))
}

// A read can lag back to the state the sandbox was in before the operation, and
// for a pause or a joined start that state is also the operation's own failure
// state. Only long-poll reads decide it.
var preOperationCases = []struct {
	name   string
	verb   string
	before []string
	frame  stateObservation
	stale  string
	target SandboxState
}{
	{"pause/running", "pause", nil, stateObservation{State: SandboxStatePaused, RawState: "paused"}, "running", SandboxStatePaused},
	{"joined-start/stopped", "start", []string{"booting"}, stateObservation{State: SandboxStateStarted, RawState: "running"}, "stopped", SandboxStateStarted},
	{"joined-start/paused", "start", []string{"booting"}, stateObservation{State: SandboxStateStarted, RawState: "running"}, "paused", SandboxStateStarted},
}

func runPreOperation(t *testing.T, verb string, statuses []string, frames ...stateObservation) (*Sandbox, *fakeServer, error) {
	t.Helper()
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx":          scriptedReads(t, statuses...),
		"POST /sandboxes/sbx/" + verb: jsonHandler(t, map[string]string{}),
	})
	sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
	require.NoError(t, err)
	sandbox.client.stateSource = eventSource(frames...)
	options := &LifecycleWaitOptions{Timeout: 30 * time.Second}
	if verb == "pause" {
		return sandbox, fake, sandbox.Pause(t.Context(), options)
	}
	return sandbox, fake, sandbox.Start(t.Context(), options)
}

func TestLifecycle_whenTargetEventReadShowsPreOperationState_waitsForTarget(t *testing.T) {
	for _, test := range preOperationCases {
		t.Run(test.name, func(t *testing.T) {
			// Given: the read after the target event shows the state before the operation.
			statuses := append(append([]string{}, test.before...), test.stale, test.frame.RawState)
			// When
			sandbox, fake, err := runPreOperation(t, test.verb, statuses, test.frame)
			// Then: a long poll decides, and it shows the target.
			require.NoError(t, err)
			require.Equal(t, test.target, sandbox.State())
			got := reads(fake)[len(test.before):]
			require.Len(t, got, 2)
			require.Empty(t, got[0].Query)
			require.Equal(t, test.frame.RawState, got[1].Query.Get("wait"))
		})
	}
}

func TestLifecycle_whenHeldEventShowsPreOperationState_longPollsDecide(t *testing.T) {
	for _, test := range preOperationCases {
		t.Run(test.name, func(t *testing.T) {
			// Given: the hub still holds the state before the operation, and the
			// read that confirms it lags too.
			held := stateObservation{State: map[string]SandboxState{
				"running": SandboxStateStarted, "stopped": SandboxStateStopped, "paused": SandboxStatePaused,
			}[test.stale], RawState: test.stale}
			statuses := append(append([]string{}, test.before...), test.stale, test.frame.RawState)
			// When
			sandbox, fake, err := runPreOperation(t, test.verb, statuses, held)
			// Then
			require.NoError(t, err)
			require.Equal(t, test.target, sandbox.State())
			got := reads(fake)
			require.Equal(t, test.frame.RawState, got[len(got)-1].Query.Get("wait"))
		})
	}
}

func TestLifecycle_whenLongPollShowsPreOperationState_returnsTerminalError(t *testing.T) {
	for _, test := range preOperationCases {
		t.Run(test.name, func(t *testing.T) {
			// Given: the operation failed; every read shows the state before it.
			statuses := append(append([]string{}, test.before...), test.stale)
			// When
			_, fake, err := runPreOperation(t, test.verb, statuses, test.frame)
			// Then: the long poll reports the failure.
			var terminal *SandboxTerminalStateError
			require.ErrorAs(t, err, &terminal)
			require.Equal(t, test.stale, terminal.RawState)
			got := reads(fake)
			require.Equal(t, test.frame.RawState, got[len(got)-1].Query.Get("wait"))
		})
	}
}

func TestLifecycle_whenHeldFailureStateIsConfirmedByEveryRead_longPollReturnsTerminalError(t *testing.T) {
	for _, test := range preOperationCases {
		t.Run(test.name, func(t *testing.T) {
			// Given: the operation failed; the hub reports the state before it
			// (for a pause, started) and every read agrees.
			held := stateObservation{State: map[string]SandboxState{
				"running": SandboxStateStarted, "stopped": SandboxStateStopped, "paused": SandboxStatePaused,
			}[test.stale], RawState: test.stale}
			statuses := append(append([]string{}, test.before...), test.stale)
			// When
			_, fake, err := runPreOperation(t, test.verb, statuses, held)
			// Then: the confirming plain read does not decide it; the long poll does.
			var terminal *SandboxTerminalStateError
			require.ErrorAs(t, err, &terminal)
			require.Equal(t, test.stale, terminal.RawState)
			got := reads(fake)[len(test.before):]
			require.Len(t, got, 2)
			require.Empty(t, got[0].Query)
			require.Equal(t, test.frame.RawState, got[1].Query.Get("wait"))
		})
	}
}

func TestLifecycle_whenTargetEventReadIsIrreversible_returnsTerminalErrorAtOnce(t *testing.T) {
	// Given
	sandbox, fake, err := runPreOperation(t, "pause", []string{"deleting"},
		stateObservation{State: SandboxStatePaused, RawState: "paused"})
	// Then
	var terminal *SandboxTerminalStateError
	require.ErrorAs(t, err, &terminal)
	require.Equal(t, SandboxStateDestroying, terminal.State)
	require.Equal(t, SandboxStateDestroying, sandbox.State())
	require.Len(t, reads(fake), 1)
}
