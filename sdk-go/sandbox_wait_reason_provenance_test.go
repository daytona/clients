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

			// Then: both full-read paths restore the matching read's explanation.
			var terminal *SandboxTerminalStateError
			require.ErrorAs(t, err, &terminal)
			require.Equal(t, "read reason", terminal.Reason)
			require.Equal(t, "read reason", sandbox.Info().Error)
		})
	}
}
