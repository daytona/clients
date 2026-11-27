// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"errors"
	"net/http"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestWait_whenEventRefreshFails_updatesObservedReason(t *testing.T) {
	for _, test := range []struct {
		name  string
		event stateObservation
	}{
		{"failed", stateObservation{State: SandboxStateError, RawState: "failed", Reason: "boot failed"}},
		{"lost", stateObservation{State: SandboxStateError, RawState: "lost", Reason: "host unavailable"}},
		{"healthy", stateObservation{State: SandboxStateStarted, RawState: "running"}},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given: the last read's reason belongs to an older observation.
			sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(http.StatusServiceUnavailable)
			})
			sandbox.info.State, sandbox.info.RawState, sandbox.info.Error = SandboxStateError, "failed", "old reason"
			source := &waitTestSource{events: make(chan stateObservation, 1)}
			source.events <- test.event
			sandbox.client.stateSource = source

			// When
			err := sandbox.Wait(t.Context(), nil)

			// Then: both the handle and the returned failure describe this event.
			if test.event.State == SandboxStateStarted {
				require.NoError(t, err)
			} else {
				var terminal *SandboxTerminalStateError
				require.ErrorAs(t, err, &terminal)
				require.Equal(t, test.event.Reason, terminal.Reason)
				require.Equal(t, test.event.RawState, terminal.RawState)
			}
			info := sandbox.Info()
			require.Equal(t, test.event.State, info.State)
			require.Equal(t, test.event.RawState, info.RawState)
			require.Equal(t, test.event.Reason, info.Error)
		})
	}
}

func TestWait_whenReasonBearingTerminalEventIsStale_acceptsAuthoritativeRead(t *testing.T) {
	// Given: even an explained failure can predate the current operation.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx": jsonHandler(t, sandboxFixture("sbx")),
	})
	sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
	require.NoError(t, err)
	source := &waitTestSource{events: make(chan stateObservation, 1)}
	source.events <- stateObservation{State: SandboxStateError, RawState: "failed", Reason: "earlier failure"}
	sandbox.client.stateSource = source

	// When
	err = sandbox.Wait(t.Context(), nil)

	// Then: the plain confirming read decides, just as for a slim event.
	require.NoError(t, err)
	require.Equal(t, SandboxStateStarted, sandbox.State())
	require.Empty(t, sandbox.Info().Error)
	requests := fake.requests()
	require.Len(t, requests, 1)
	require.Equal(t, http.MethodGet, requests[0].Method)
	require.Empty(t, requests[0].Query)
}

func TestWait_whenPollIsRateLimited_retriesAfterTheRetryHint(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given: the server supplies a retry hint on every read.
		reads := 0
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
			reads++
			w.Header().Set("Retry-After", "2")
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusTooManyRequests)
			writeJSON(t, w, map[string]string{
				"code": "RATE_LIMITED", "source": "DAYTONA_API", "message": "retry later",
			})
		})
		began := time.Now()

		// When
		err := sandbox.Wait(t.Context(), &WaitOptions{Timeout: 5 * time.Second})

		// Then: reads at 0, 2 and 4 s honor the hint; the wait timeout wraps the 429.
		require.ErrorIs(t, err, ErrRequestTimeout)
		var detail *Error
		require.ErrorAs(t, errors.Unwrap(err), &detail)
		require.Equal(t, http.StatusTooManyRequests, detail.StatusCode)
		require.Equal(t, "DAYTONA_API", detail.Source)
		require.Equal(t, 2*time.Second, detail.RetryAfter)
		require.Equal(t, 3, reads)
		require.Equal(t, 5*time.Second, time.Since(began))
	})
}
