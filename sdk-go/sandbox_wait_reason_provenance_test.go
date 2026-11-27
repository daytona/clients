// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestWait_whenPriorEventHasReason_doesNotBorrowForSlimTerminalEvent(t *testing.T) {
	// Given: a previous wait observed a reason-bearing event, but no full read.
	sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	})
	source := &waitTestSource{events: make(chan stateObservation, 1)}
	sandbox.client.stateSource = source
	source.events <- stateObservation{State: SandboxStateError, RawState: "failed", Reason: "earlier event"}
	var previous *SandboxTerminalStateError
	require.ErrorAs(t, sandbox.Wait(t.Context(), nil), &previous)
	require.Equal(t, "earlier event", previous.Reason)
	source.events <- stateObservation{State: SandboxStateError, RawState: "failed"}

	// When: the new slim event matches the old event's state and wire status.
	err := sandbox.Wait(t.Context(), nil)

	// Then: a prior event's explanation is not a read-backed fallback.
	var terminal *SandboxTerminalStateError
	require.ErrorAs(t, err, &terminal)
	require.Equal(t, "failed", terminal.RawState)
	require.Empty(t, terminal.Reason)
	require.Empty(t, sandbox.Info().Error)
}

func TestWait_whenFullReadFollowsEvent_restoresReasonProvenance(t *testing.T) {
	for _, read := range []string{"refresh", "wait"} {
		t.Run(read, func(t *testing.T) {
			// Given: event-backed state followed by an authoritative read.
			reads := 0
			sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
				reads++
				if reads != 2 {
					w.WriteHeader(http.StatusServiceUnavailable)
					return
				}
				detail := sandboxFixture("sbx")
				detail["status"], detail["error"] = "failed", "read reason"
				writeJSON(t, w, detail)
			})
			source := &waitTestSource{events: make(chan stateObservation, 1)}
			sandbox.client.stateSource = source
			source.events <- stateObservation{State: SandboxStateError, RawState: "failed", Reason: "event reason"}
			require.Error(t, sandbox.Wait(t.Context(), nil))
			switch read {
			case "refresh":
				require.NoError(t, sandbox.Refresh(t.Context()))
			case "wait":
				sandbox.client.stateSource = noopStateSource{}
				require.Error(t, sandbox.Wait(t.Context(), nil))
				sandbox.client.stateSource = source
			}
			source.events <- stateObservation{State: SandboxStateError, RawState: "failed"}

			// When
			err := sandbox.Wait(t.Context(), nil)

			// Then: both full-read paths restore the matching read's explanation
			// on the raised error; the handle records the event verbatim, so
			// its absent message clears the handle's.
			var terminal *SandboxTerminalStateError
			require.ErrorAs(t, err, &terminal)
			require.Equal(t, "read reason", terminal.Reason)
			require.Empty(t, sandbox.Info().Error)
		})
	}
}

func TestWait_whenTerminalEventHasNoReason_handleTakesItVerbatim(t *testing.T) {
	for _, test := range []struct {
		name     string
		before   string
		frame    string
		reason   string
		handle   string
		borrowed string
	}{
		// A full read of error/failed with a message, then a slim error/failed event.
		{"same-state", "failed", "", "", "", "read reason"},
		// The same read, then an event of the same state carrying its own message.
		{"frame-message", "failed", "the guest crashed", "the guest crashed", "the guest crashed", "the guest crashed"},
		// A read of another wire status: nothing is borrowed.
		{"other-wire-status", "lost", "", "", "", ""},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given: a full read, then a terminal event whose confirming read fails.
			reads := 0
			sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
				reads++
				if reads > 1 {
					w.WriteHeader(http.StatusServiceUnavailable)
					return
				}
				detail := sandboxFixture("sbx")
				detail["status"], detail["error"] = test.before, "read reason"
				writeJSON(t, w, detail)
			})
			require.NoError(t, sandbox.Refresh(t.Context()))
			source := &waitTestSource{events: make(chan stateObservation, 1)}
			sandbox.client.stateSource = source
			source.events <- stateObservation{State: SandboxStateError, RawState: "failed", Reason: test.frame}
			// When
			err := sandbox.Wait(t.Context(), nil)
			// Then: the handle takes the event verbatim; only the raised error
			// borrows a matching read's reason.
			var terminal *SandboxTerminalStateError
			require.ErrorAs(t, err, &terminal)
			require.Equal(t, test.borrowed, terminal.Reason)
			require.Equal(t, test.handle, sandbox.Info().Error)
			require.Equal(t, 2, reads)
		})
	}
}
