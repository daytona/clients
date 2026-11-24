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

func TestWait_whenProbeReturnsNonterminal_rearmsWithMinimumSpacing(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given: a quiet socket and one fast, nonterminal read before the target.
		began := time.Now()
		var reads []time.Duration
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
			reads = append(reads, time.Since(began))
			detail := sandboxFixture("sbx")
			if len(reads) == 1 {
				detail["status"] = "booting"
			}
			writeJSON(t, w, detail)
		})
		sandbox.client.stateSource = &waitTestSource{events: make(chan stateObservation)}

		// When
		err := sandbox.Wait(t.Context(), nil)

		// Then: do not wait a new ten seconds, nor spin immediately on the quiet socket.
		require.NoError(t, err)
		require.Equal(t, []time.Duration{10 * time.Second, 10200 * time.Millisecond}, reads)
	})
}

func TestWait_whenJitterFiresEarly_stillRequiresFullSilence(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given: jitter always selects the lower end of its window.
		began := time.Now()
		var reads []time.Duration
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
			reads = append(reads, time.Since(began))
			writeJSON(t, w, sandboxFixture("sbx"))
		})
		sandbox.client.stateSource = &waitTestSource{
			events: make(chan stateObservation),
			jitter: func(d time.Duration) time.Duration { return d * 9 / 10 },
		}

		// When
		err := sandbox.Wait(t.Context(), nil)

		// Then: an early timer rechecks silence, including sub-nanosecond rounding.
		require.NoError(t, err)
		require.Equal(t, []time.Duration{10 * time.Second}, reads)
	})
}

func TestWait_whenFailedProbeRearms_appliesFreshJitter(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given: first arm uses +10%, retry uses -10%.
		began := time.Now()
		var reads []time.Duration
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
			reads = append(reads, time.Since(began))
			if len(reads) == 1 {
				w.WriteHeader(http.StatusServiceUnavailable)
				return
			}
			writeJSON(t, w, sandboxFixture("sbx"))
		})
		arms := 0
		sandbox.client.stateSource = &waitTestSource{
			events: make(chan stateObservation),
			jitter: func(d time.Duration) time.Duration {
				arms++
				if arms == 1 {
					return d * 11 / 10
				}
				return d * 9 / 10
			},
		}

		// When
		err := sandbox.Wait(t.Context(), nil)

		// Then: failed polls use fresh jitter, not a retained sample or backoff.
		require.NoError(t, err)
		require.Equal(t, []time.Duration{11 * time.Second, 20 * time.Second}, reads)
	})
}
