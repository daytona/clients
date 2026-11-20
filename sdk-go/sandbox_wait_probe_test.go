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

func TestWait_whenSocketHealthyWithoutTransitions_readsOnlyFinalReserve(t *testing.T) {
	for _, targets := range [][]SandboxState{
		{SandboxStateStarted}, {SandboxStateStarted, SandboxStateStopped},
	} {
		t.Run(string(targets[len(targets)-1]), func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given: no state transitions, but a socket frame every four seconds.
				began := time.Now()
				var reads []time.Duration
				sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
					reads = append(reads, time.Since(began))
					detail := sandboxFixture("sbx")
					if time.Since(began) < 55*time.Second {
						detail["status"] = "booting"
					}
					writeJSON(t, w, detail)
				})
				sandbox.client.stateSource = &waitTestSource{
					events: make(chan stateObservation),
					lastFrame: func() time.Time {
						return began.Add(time.Since(began).Truncate(4 * time.Second))
					},
				}

				// When
				err := sandbox.Wait(t.Context(), &WaitOptions{Until: targets, Timeout: time.Minute})

				// Then: liveness suppresses probes, not the authoritative final read.
				require.NoError(t, err)
				require.Equal(t, []time.Duration{55 * time.Second}, reads)
			})
		})
	}
}

func TestWait_whenSocketGoesQuiet_probesAfterRemainingSilence(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given: frames stop at eight seconds, before the first probe timer fires.
		began := time.Now()
		var reads []time.Duration
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
			reads = append(reads, time.Since(began))
			writeJSON(t, w, sandboxFixture("sbx"))
		})
		sandbox.client.stateSource = &waitTestSource{
			events: make(chan stateObservation),
			lastFrame: func() time.Time {
				return began.Add(min(8*time.Second, time.Since(began).Truncate(4*time.Second)))
			},
		}

		// When
		err := sandbox.Wait(t.Context(), nil)

		// Then: exactly one authoritative probe settles without a metadata refresh.
		require.NoError(t, err)
		require.Equal(t, []time.Duration{18 * time.Second}, reads)
	})
}

func TestWait_whenShortBudget_probesOnlyWithoutFramesSinceRegistration(t *testing.T) {
	for _, hasFrame := range []bool{false, true} {
		synctest.Test(t, func(t *testing.T) {
			// Given: a short wait whose socket may receive one frame at 100ms.
			began := time.Now()
			var reads []time.Duration
			sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
				reads = append(reads, time.Since(began))
				writeJSON(t, w, sandboxFixture("sbx"))
			})
			sandbox.client.stateSource = &waitTestSource{
				events: make(chan stateObservation),
				lastFrame: func() time.Time {
					if hasFrame && time.Since(began) >= 100*time.Millisecond {
						return began.Add(100 * time.Millisecond)
					}
					return began.Add(-time.Hour) // An older shared socket must not accelerate this watch.
				},
			}

			// When
			err := sandbox.Wait(t.Context(), &WaitOptions{Timeout: time.Second})

			// Then: use half the hub share once, or wait for the final reserve.
			require.NoError(t, err)
			expected := 375 * time.Millisecond
			if hasFrame {
				expected = 750 * time.Millisecond
			}
			require.Equal(t, []time.Duration{expected}, reads)
		})
	}
}

func TestWait_whenFrameArrivesDuringProbe_discardsNonterminalRead(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given: the GET from 10s to 12s overlaps a socket frame at 11s.
		began := time.Now()
		var reads []time.Duration
		var nameBeforeNextRead string
		var sandbox *Sandbox
		sandbox = virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
			reads = append(reads, time.Since(began))
			detail := sandboxFixture("sbx")
			if len(reads) == 1 {
				timer := time.NewTimer(2 * time.Second)
				defer timer.Stop()
				<-timer.C
				detail["status"], detail["name"] = "booting", "stale"
			} else {
				nameBeforeNextRead = sandbox.Info().Name
			}
			writeJSON(t, w, detail)
		})
		sandbox.client.stateSource = &waitTestSource{
			events: make(chan stateObservation),
			lastFrame: func() time.Time {
				if time.Since(began) >= 11*time.Second {
					return began.Add(11 * time.Second)
				}
				return time.Time{}
			},
		}

		// When
		err := sandbox.Wait(t.Context(), nil)

		// Then: discard stale metadata and rearm from the frame, not the GET end.
		require.NoError(t, err)
		require.Equal(t, []time.Duration{10 * time.Second, 21 * time.Second}, reads)
		require.Empty(t, nameBeforeNextRead)
	})
}

func TestWait_whenProbeFails_rearmsFreshDelayFromCompletion(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given: a failed two-second GET on a quiet socket.
		began := time.Now()
		var reads []time.Duration
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
			reads = append(reads, time.Since(began))
			if len(reads) == 1 {
				timer := time.NewTimer(2 * time.Second)
				defer timer.Stop()
				<-timer.C
				w.WriteHeader(http.StatusServiceUnavailable)
				return
			}
			writeJSON(t, w, sandboxFixture("sbx"))
		})
		sandbox.client.stateSource = &waitTestSource{events: make(chan stateObservation)}

		// When
		err := sandbox.Wait(t.Context(), nil)

		// Then: a new ten-second delay, not the remaining silence or 200ms backoff.
		require.NoError(t, err)
		require.Equal(t, []time.Duration{10 * time.Second, 22 * time.Second}, reads)
	})
}
