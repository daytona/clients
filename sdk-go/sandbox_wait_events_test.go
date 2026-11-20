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

func TestWait_whenEventTerminal_isConfirmedByRead(t *testing.T) {
	for _, status := range []string{"running", "booting", "failed", "lost", "deleting", "404", "500"} {
		t.Run(status, func(t *testing.T) {
			// Given: a stale failed frame and a fresh API observation.
			var reads atomic.Int32
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx": func(w http.ResponseWriter, _ *http.Request) {
				detail := sandboxFixture("sbx")
				if reads.Add(1) == 1 {
					switch status {
					case "404":
						w.WriteHeader(http.StatusNotFound)
						return
					case "500":
						w.WriteHeader(http.StatusInternalServerError)
						return
					default:
						detail["status"], detail["error"] = status, "fresh reason"
					}
				}
				writeJSON(t, w, detail)
			}})
			sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
			require.NoError(t, err)
			source := &waitTestSource{events: make(chan stateObservation, 1)}
			source.events <- stateObservation{State: SandboxStateError, RawState: "failed"}
			sandbox.client.stateSource = source
			// When
			err = sandbox.Wait(t.Context(), nil)
			// Then: confirmation is plain; a stale nonterminal report keeps polling.
			require.Empty(t, fake.requests()[0].Query)
			if status == "running" || status == "booting" {
				require.NoError(t, err)
				require.Equal(t, SandboxStateStarted, sandbox.State())
			} else {
				var terminal *SandboxTerminalStateError
				require.ErrorAs(t, err, &terminal)
				if status == "404" || status == "500" {
					require.Equal(t, "failed", terminal.RawState)
					require.Empty(t, terminal.Reason)
				} else {
					require.Equal(t, status, terminal.RawState)
					require.Equal(t, "fresh reason", terminal.Reason)
				}
			}
			require.EqualValues(t, 1, source.released.Load())
		})
	}
}

func TestWait_whenTargetEventObserved_refreshIsBestEffort(t *testing.T) {
	for _, status := range []string{"stopping", "500"} {
		t.Run(status, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx": func(w http.ResponseWriter, _ *http.Request) {
				if status == "500" {
					w.WriteHeader(http.StatusInternalServerError)
					return
				}
				detail := sandboxFixture("sbx")
				detail["status"] = status
				writeJSON(t, w, detail)
			}})
			sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
			require.NoError(t, err)
			sandbox.info.Region = " Local "
			source := &waitTestSource{events: make(chan stateObservation, 1)}
			source.events <- stateObservation{State: SandboxStateStarted, RawState: "running"}
			sandbox.client.stateSource = source
			// When
			err = sandbox.Wait(t.Context(), nil)
			// Then
			require.NoError(t, err)
			expected := SandboxStateStopping
			if status == "500" {
				expected = SandboxStateStarted
			}
			require.Equal(t, expected, sandbox.State())
			require.Empty(t, source.region)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestWait_whenEventsSilent_readsWithinFinalReserve(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		began := time.Now()
		var readAt time.Duration
		var held string
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, r *http.Request) {
			readAt, held = time.Since(began), r.URL.Query().Get("timeout_ms")
			writeJSON(t, w, sandboxFixture("sbx"))
		})
		sandbox.client.stateSource = &waitTestSource{events: make(chan stateObservation)}
		// When
		err := sandbox.Wait(t.Context(), &WaitOptions{Timeout: time.Second})
		// Then: the stream gets 3/4 of the budget, never the whole budget.
		require.NoError(t, err)
		require.Equal(t, 750*time.Millisecond, readAt)
		require.Equal(t, "250", held)
		require.Equal(t, SandboxStateStarted, sandbox.State())
	})
}

func TestWait_whenFinalReadFails_preservesTimeoutCause(t *testing.T) {
	for _, status := range []int{401, 403, 404, 408, 500, 503} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given
				sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(status) })
				sandbox.client.stateSource = &waitTestSource{events: make(chan stateObservation)}
				// When
				err := sandbox.Wait(t.Context(), &WaitOptions{Timeout: time.Second})
				// Then
				var sdkError *Error
				require.ErrorAs(t, err, &sdkError)
				if status == 401 || status == 403 || status == 404 {
					require.Equal(t, status, sdkError.StatusCode)
				} else {
					require.Equal(t, CodeRequestTimeout, sdkError.Code)
					require.Equal(t, "SDK", sdkError.Source)
					var cause *Error
					require.ErrorAs(t, sdkError.Err, &cause)
					require.Equal(t, status, cause.StatusCode)
				}
			})
		})
	}
}

func TestWait_whenEventArrivesDuringProbe_cancelsAndJoinsRead(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		source := &waitTestSource{events: make(chan stateObservation, 1)}
		var cancelled atomic.Bool
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Query().Get("wait") != "" {
				source.events <- stateObservation{State: SandboxStateStarted, RawState: "running"}
				<-r.Context().Done()
				cancelled.Store(true)
				return
			}
			writeJSON(t, w, sandboxFixture("sbx"))
		})
		sandbox.client.stateSource = source
		// When
		err := sandbox.Wait(t.Context(), &WaitOptions{Timeout: WaitForever})
		// Then
		require.NoError(t, err)
		require.True(t, cancelled.Load())
		require.Equal(t, SandboxStateStarted, sandbox.State())
		require.EqualValues(t, 1, source.released.Load())
	})
}

func TestWait_whenEventSourceCloses_fallsBackImmediately(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
			writeJSON(t, w, sandboxFixture("sbx"))
		})
		source := &waitTestSource{events: make(chan stateObservation)}
		close(source.events)
		sandbox.client.stateSource = source
		began := time.Now()
		// When
		err := sandbox.Wait(t.Context(), nil)
		// Then
		require.NoError(t, err)
		require.Zero(t, time.Since(began))
	})
}
