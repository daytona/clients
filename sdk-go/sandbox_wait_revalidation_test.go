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

// Scripted state sequences for the sandbox waits: a target event ends the wait
// with no read, except for a macOS start or resume, whose read after it decides
// like any other; Start that joins a start or resume another caller sent ends
// when that operation fails. The Python and TypeScript
// twins are test_wait_target_revalidation.py and wait-target-revalidation.test.ts.

// scriptedReads answers each GET with the next status; the last one repeats.
func scriptedReads(t *testing.T, statuses ...string) http.HandlerFunc {
	t.Helper()
	return scriptedClassReads(t, SandboxClassLinux, statuses...)
}

// scriptedClassReads is scriptedReads for a sandbox of the given class.
func scriptedClassReads(t *testing.T, class SandboxClass, statuses ...string) http.HandlerFunc {
	t.Helper()
	var reads atomic.Int32
	return func(w http.ResponseWriter, _ *http.Request) {
		index := min(int(reads.Add(1)), len(statuses)) - 1
		detail := sandboxFixture("sbx")
		detail["class"] = string(class)
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

// macOSSandbox is a lifecycle handle that knows its class is macOS.
func macOSSandbox(t *testing.T, fake *fakeServer) *Sandbox {
	t.Helper()
	sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
	require.NoError(t, err)
	sandbox.info.Class = SandboxClassMacOS
	return sandbox
}

func TestLifecycle_whenTargetEventObserved_readsNothingAfterIt(t *testing.T) {
	for _, test := range []struct {
		verb     string
		statuses []string
		frame    stateObservation
	}{
		{"wait", nil, stateObservation{State: SandboxStateStarted, RawState: "running"}},
		// Start reads the sandbox before it sends anything.
		{"start", []string{"stopped"}, stateObservation{State: SandboxStateStarted, RawState: "running"}},
		{"resume", nil, stateObservation{State: SandboxStateStarted, RawState: "running"}},
		{"stop", nil, stateObservation{State: SandboxStateStopped, RawState: "stopped"}},
		{"pause", nil, stateObservation{State: SandboxStatePaused, RawState: "paused"}},
		{"delete", nil, stateObservation{State: SandboxStateDestroyed, RawState: "deleted"}},
	} {
		t.Run(test.verb, func(t *testing.T) {
			// Given: every later read would show a state no lag can show.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx":               scriptedReads(t, append(append([]string{}, test.statuses...), "deleting")...),
				"POST /sandboxes/sbx/" + test.verb: jsonHandler(t, map[string]string{}),
				"DELETE /sandboxes/sbx":            jsonHandler(t, map[string]string{}),
			})
			sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
			require.NoError(t, err)
			sandbox.client.stateSource = eventSource(test.frame)
			options := &LifecycleWaitOptions{Timeout: 30 * time.Second}
			// When
			switch test.verb {
			case "wait":
				err = sandbox.Wait(t.Context(), &WaitOptions{Timeout: 30 * time.Second})
			case "start":
				err = sandbox.Start(t.Context(), options)
			case "resume":
				err = sandbox.Resume(t.Context(), options)
			case "stop":
				err = sandbox.Stop(t.Context(), options)
			case "pause":
				err = sandbox.Pause(t.Context(), options)
			case "delete":
				err = sandbox.Delete(t.Context(), options)
			}
			// Then: the event decides; only Start's own first read was made.
			require.NoError(t, err)
			require.Equal(t, test.frame.State, sandbox.State())
			require.Len(t, reads(fake), len(test.statuses))
		})
	}
}

func TestResume_whenMacOSTargetEventReadDisagrees_waitsForTarget(t *testing.T) {
	// Given: the read after the started event still shows the sandbox booting.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx":         scriptedClassReads(t, SandboxClassMacOS, "booting", "booting", "running"),
		"POST /sandboxes/sbx/resume": jsonHandler(t, map[string]string{}),
	})
	sandbox := macOSSandbox(t, fake)
	sandbox.client.stateSource = eventSource(stateObservation{State: SandboxStateStarted, RawState: "running"})
	// When
	err := sandbox.Resume(t.Context(), &LifecycleWaitOptions{Timeout: 30 * time.Second})
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

func TestResume_whenMacOSTargetEventReadIsTerminal_returnsTerminalError(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx":         scriptedClassReads(t, SandboxClassMacOS, "failed"),
		"POST /sandboxes/sbx/resume": jsonHandler(t, map[string]string{}),
	})
	sandbox := macOSSandbox(t, fake)
	sandbox.client.stateSource = eventSource(stateObservation{State: SandboxStateStarted, RawState: "running"})
	// When
	err := sandbox.Resume(t.Context(), &LifecycleWaitOptions{Timeout: 30 * time.Second})
	// Then
	var terminal *SandboxTerminalStateError
	require.ErrorAs(t, err, &terminal)
	require.Equal(t, SandboxStateError, terminal.State)
	require.Equal(t, "failed", terminal.RawState)
	require.Equal(t, "the guest crashed", terminal.Reason)
	require.Len(t, reads(fake), 1)
}

func TestResume_whenMacOSTargetEventReadNeverConverges_timesOutOnTheSameDeadline(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		read := scriptedClassReads(t, SandboxClassMacOS, "booting")
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, r *http.Request) {
			if r.Method == http.MethodPost {
				writeJSON(t, w, map[string]string{})
				return
			}
			read(w, r)
		})
		sandbox.info.Class = SandboxClassMacOS
		sandbox.client.stateSource = eventSource(stateObservation{State: SandboxStateStarted, RawState: "running"})
		began := time.Now()
		// When
		err := sandbox.Resume(t.Context(), &LifecycleWaitOptions{Timeout: 5 * time.Second})
		// Then: the budget is not restarted after the event.
		require.ErrorIs(t, err, ErrRequestTimeout)
		var detail *Error
		require.ErrorAs(t, err, &detail)
		require.Equal(t, SourceSDK, detail.Source)
		require.Equal(t, 5*time.Second, time.Since(began))
		require.Equal(t, SandboxStateStarting, sandbox.State())
	})
}

func TestLifecycle_whenMacOSTargetEventReadLags_waitsForTarget(t *testing.T) {
	for _, test := range []struct {
		verb     string
		statuses []string
		frame    stateObservation
		target   SandboxState
	}{
		// Start reads the sandbox first; the read after the event lags behind it.
		{"start", []string{"stopped", "booting", "running"}, stateObservation{State: SandboxStateStarted, RawState: "running"}, SandboxStateStarted},
		{"resume", []string{"booting", "running"}, stateObservation{State: SandboxStateStarted, RawState: "running"}, SandboxStateStarted},
	} {
		t.Run(test.verb, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx":               scriptedClassReads(t, SandboxClassMacOS, test.statuses...),
				"POST /sandboxes/sbx/" + test.verb: jsonHandler(t, map[string]string{}),
			})
			sandbox := macOSSandbox(t, fake)
			sandbox.client.stateSource = eventSource(test.frame)
			options := &LifecycleWaitOptions{Timeout: 30 * time.Second}
			// When
			var err error
			if test.verb == "start" {
				err = sandbox.Start(t.Context(), options)
			} else {
				err = sandbox.Resume(t.Context(), options)
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

// macOSPreOperationCases are the cases whose target event is followed by a read:
// only a macOS start reads after one.
func macOSPreOperationCases() []struct {
	name   string
	verb   string
	before []string
	frame  stateObservation
	stale  string
	target SandboxState
} {
	var result = preOperationCases[:0:0]
	for _, test := range preOperationCases {
		if test.verb == "start" {
			result = append(result, test)
		}
	}
	return result
}

func runPreOperation(t *testing.T, verb string, statuses []string, frames ...stateObservation) (*Sandbox, *fakeServer, error) {
	t.Helper()
	return runClassPreOperation(t, SandboxClassLinux, verb, statuses, frames...)
}

func runClassPreOperation(t *testing.T, class SandboxClass, verb string, statuses []string, frames ...stateObservation) (*Sandbox, *fakeServer, error) {
	t.Helper()
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx":          scriptedClassReads(t, class, statuses...),
		"POST /sandboxes/sbx/" + verb: jsonHandler(t, map[string]string{}),
	})
	sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
	require.NoError(t, err)
	if class == SandboxClassMacOS {
		sandbox.info.Class = class
	}
	sandbox.client.stateSource = eventSource(frames...)
	options := &LifecycleWaitOptions{Timeout: 30 * time.Second}
	switch verb {
	case "pause":
		return sandbox, fake, sandbox.Pause(t.Context(), options)
	case "resume":
		return sandbox, fake, sandbox.Resume(t.Context(), options)
	}
	return sandbox, fake, sandbox.Start(t.Context(), options)
}

func TestLifecycle_whenMacOSTargetEventReadShowsPreOperationState_waitsForTarget(t *testing.T) {
	for _, test := range macOSPreOperationCases() {
		t.Run(test.name, func(t *testing.T) {
			// Given: the read after the target event shows the state before the operation.
			statuses := append(append([]string{}, test.before...), test.stale, test.frame.RawState)
			// When
			sandbox, fake, err := runClassPreOperation(t, SandboxClassMacOS, test.verb, statuses, test.frame)
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

func TestLifecycle_whenMacOSLongPollShowsPreOperationState_returnsTerminalError(t *testing.T) {
	for _, test := range macOSPreOperationCases() {
		t.Run(test.name, func(t *testing.T) {
			// Given: the operation failed; every read shows the state before it.
			statuses := append(append([]string{}, test.before...), test.stale)
			// When
			_, fake, err := runClassPreOperation(t, SandboxClassMacOS, test.verb, statuses, test.frame)
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

func TestResume_whenMacOSTargetEventReadIsIrreversible_returnsTerminalErrorAtOnce(t *testing.T) {
	// Given
	sandbox, fake, err := runClassPreOperation(t, SandboxClassMacOS, "resume", []string{"deleting"},
		stateObservation{State: SandboxStateStarted, RawState: "running"})
	// Then
	var terminal *SandboxTerminalStateError
	require.ErrorAs(t, err, &terminal)
	require.Equal(t, SandboxStateDestroying, terminal.State)
	require.Equal(t, SandboxStateDestroying, sandbox.State())
	require.Len(t, reads(fake), 1)
}

func TestLifecycle_whenConfirmingReadHandsOverToPolling_firstLongPollKeepsSpacing(t *testing.T) {
	for _, test := range []struct {
		name     string
		verb     string
		class    SandboxClass
		statuses []string
		target   SandboxState
	}{
		// A pause whose events report still holds started, its failure state.
		{"terminal-report", "pause", SandboxClassLinux, []string{"running", "paused"}, SandboxStatePaused},
		// A macOS resume whose read after the target report still shows paused.
		{"target-report", "resume", SandboxClassMacOS, []string{"paused", "running"}, SandboxStateStarted},
	} {
		t.Run(test.name, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given: the read that confirms the report shows the state before the operation.
				var stamps []time.Time
				var holds []string
				read := scriptedClassReads(t, test.class, test.statuses...)
				sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, r *http.Request) {
					if r.Method == http.MethodPost {
						writeJSON(t, w, map[string]string{})
						return
					}
					stamps = append(stamps, time.Now())
					holds = append(holds, r.URL.Query().Get("wait"))
					read(w, r)
				})
				sandbox.info.Class = test.class
				sandbox.client.stateSource = eventSource(stateObservation{State: SandboxStateStarted, RawState: "running"})
				options := &LifecycleWaitOptions{Timeout: 30 * time.Second}
				// When
				var err error
				if test.verb == "pause" {
					err = sandbox.Pause(t.Context(), options)
				} else {
					err = sandbox.Resume(t.Context(), options)
				}
				// Then: the first long poll waits out the minimum spacing.
				require.NoError(t, err)
				require.Equal(t, test.target, sandbox.State())
				require.Len(t, stamps, 2)
				require.GreaterOrEqual(t, stamps[1].Sub(stamps[0]), waitMinInterval)
				require.Equal(t, []string{"", test.statuses[1]}, holds)
			})
		})
	}
}

func TestLifecycle_whenConfirmingReadLeavesLessThanSpacing_firstLongPollReadsAtOnce(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given: the read after a macOS resume's target event takes 900 ms of a
		// 1 s budget and shows the state before the resume, leaving less than
		// the minimum spacing.
		began := time.Now()
		var stamps []time.Duration
		var holds []string
		read := scriptedClassReads(t, SandboxClassMacOS, "paused", "running")
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, r *http.Request) {
			if r.Method == http.MethodPost {
				writeJSON(t, w, map[string]string{})
				return
			}
			stamps = append(stamps, time.Since(began))
			holds = append(holds, r.URL.Query().Get("wait"))
			if len(stamps) == 1 {
				time.Sleep(900 * time.Millisecond)
			}
			read(w, r)
		})
		sandbox.info.Class = SandboxClassMacOS
		sandbox.client.stateSource = eventSource(stateObservation{State: SandboxStateStarted, RawState: "running"})
		// When
		err := sandbox.Resume(t.Context(), &LifecycleWaitOptions{Timeout: time.Second})
		// Then: the long poll reads at once instead of being pushed past the deadline.
		require.NoError(t, err)
		require.Equal(t, SandboxStateStarted, sandbox.State())
		require.Equal(t, []time.Duration{0, 900 * time.Millisecond}, stamps)
		require.Equal(t, []string{"", "running"}, holds)
	})
}

func TestClientResume_whenAReadDuringTheWaitShowsMacOS_readsAfterTheTargetEvent(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given: a resume by ID (the handle knows no class) whose early probe
		// reads a booting macOS sandbox before the started event arrives.
		source := &waitTestSource{events: make(chan stateObservation, 1)}
		probed := make(chan struct{})
		var holds []string
		read := scriptedClassReads(t, SandboxClassMacOS, "booting", "running")
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, r *http.Request) {
			if r.Method == http.MethodPost {
				writeJSON(t, w, map[string]string{})
				return
			}
			holds = append(holds, r.URL.Query().Get("wait"))
			read(w, r)
			if len(holds) == 1 {
				close(probed)
			}
		})
		sandbox.client.stateSource = source
		// When
		done := make(chan error, 1)
		go func() { done <- sandbox.client.Resume(t.Context(), "sbx", nil) }()
		<-probed
		synctest.Wait()
		source.events <- stateObservation{State: SandboxStateStarted, RawState: "running"}
		// Then: the class the probe read asks for the macOS read after the event.
		require.NoError(t, <-done)
		require.Len(t, holds, 2)
		require.Empty(t, holds[1])
	})
}

func TestClientResume_whenNoReadLearnedTheClass_readsNothingAfterTheTargetEvent(t *testing.T) {
	// Given: a resume by ID decided by the events stream before any read.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx":         scriptedClassReads(t, SandboxClassMacOS, "running"),
		"POST /sandboxes/sbx/resume": jsonHandler(t, map[string]string{}),
	})
	client := newTestClient(t, fake.Server)
	client.stateSource = eventSource(stateObservation{State: SandboxStateStarted, RawState: "running"})
	// When
	err := client.Resume(t.Context(), "sbx", nil)
	// Then: the handle the call builds is never returned, and it knows no class.
	require.NoError(t, err)
	require.Empty(t, reads(fake))
}
