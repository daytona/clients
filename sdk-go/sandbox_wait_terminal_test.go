// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestDelete_whenTerminalEventIsStale_acceptsAuthoritativeNotFound(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"DELETE /sandboxes/sbx": jsonHandler(t, map[string]string{}),
		"GET /sandboxes/sbx": func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusNotFound)
		},
	})
	sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
	require.NoError(t, err)
	source := &waitTestSource{events: make(chan stateObservation, 1)}
	source.events <- stateObservation{State: SandboxStateError, RawState: "failed"}
	sandbox.client.stateSource = source
	// When
	err = sandbox.Delete(t.Context(), nil)
	// Then
	require.NoError(t, err)
	require.Equal(t, SandboxStateDestroyed, sandbox.State())
	require.Empty(t, fake.requests()[1].Query)
}

func TestWait_whenDestroyedEvent_skipsMetadataRead(t *testing.T) {
	for _, target := range []SandboxState{SandboxStateStarted, SandboxStateDestroyed} {
		t.Run(string(target), func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
			require.NoError(t, err)
			source := &waitTestSource{events: make(chan stateObservation, 1)}
			source.events <- stateObservation{State: SandboxStateDestroyed, RawState: "deleted"}
			sandbox.client.stateSource = source
			// When
			err = sandbox.Wait(t.Context(), &WaitOptions{Until: []SandboxState{target}})
			// Then
			if target == SandboxStateStarted {
				var terminal *SandboxTerminalStateError
				require.ErrorAs(t, err, &terminal)
				require.Equal(t, "deleted", terminal.RawState)
			} else {
				require.NoError(t, err)
			}
			require.Empty(t, fake.requests())
		})
	}
}

func TestWait_whenNoWait_preservesCurrentObservation(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
	require.NoError(t, err)
	// When
	err = sandbox.Wait(t.Context(), &WaitOptions{Timeout: NoWait})
	// Then
	require.NoError(t, err)
	require.Equal(t, SandboxStateUnknown, sandbox.State())
	require.Empty(t, fake.requests())
}

func TestWait_whenDestroyedTargetStillExistsAt404_returnsNotFound(t *testing.T) {
	// Given: unlike Delete, plain Wait does not reinterpret a missing resource.
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx": func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}})
	sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
	require.NoError(t, err)
	// When
	err = sandbox.Wait(t.Context(), &WaitOptions{Until: []SandboxState{SandboxStateDestroyed}})
	// Then
	require.ErrorIs(t, err, ErrNotFound)
}

func TestWait_whenSilentSourceNeverReachesTarget_spendsFinalBudget(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
			detail := sandboxFixture("sbx")
			detail["status"] = "booting"
			writeJSON(t, w, detail)
		})
		sandbox.client.stateSource = &waitTestSource{events: make(chan stateObservation)}
		began := time.Now()
		// When
		err := sandbox.Wait(t.Context(), &WaitOptions{Timeout: time.Second})
		// Then
		require.ErrorIs(t, err, ErrRequestTimeout)
		require.Equal(t, time.Second, time.Since(began))
	})
}
