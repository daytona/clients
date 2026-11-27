// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"errors"
	"net/http"
	"syscall"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestWaitRetry_whenDecisiveRead_stops(t *testing.T) {
	for _, live := range []bool{true, false} {
		for _, test := range []struct {
			name     string
			status   int
			raw      string
			want     error
			deleting bool
		}{
			{"authentication", 401, "", ErrAuthentication, false},
			{"forbidden", 403, "", ErrForbidden, false},
			{"not_found", 404, "", ErrNotFound, false},
			{"delete_not_found", 404, "", nil, true},
			{"failed", 200, "failed", ErrConflict, false},
			{"lost", 200, "lost", ErrConflict, false},
		} {
			t.Run(test.name, func(t *testing.T) {
				synctest.Test(t, func(t *testing.T) {
					// Given: a transient failure followed by a decisive read.
					reads := 0
					sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, r *http.Request) {
						if r.Method == http.MethodDelete {
							writeJSON(t, w, struct{}{})
							return
						}
						reads++
						if reads == 1 {
							w.WriteHeader(http.StatusServiceUnavailable)
							return
						}
						if test.status != http.StatusOK {
							w.WriteHeader(test.status)
							return
						}
						detail := sandboxFixture("sbx")
						detail["status"], detail["error"] = test.raw, "observed reason"
						writeJSON(t, w, detail)
					})
					source := &waitTestSource{events: make(chan stateObservation)}
					if !live {
						close(source.events)
					}
					sandbox.client.stateSource = source

					// When
					var err error
					if test.deleting {
						err = sandbox.Delete(t.Context(), nil)
					} else {
						err = sandbox.Wait(t.Context(), nil)
					}

					// Then: no retry can override the authoritative terminal answer.
					if test.want == nil {
						require.NoError(t, err)
						require.Equal(t, SandboxStateDestroyed, sandbox.State())
					} else {
						require.ErrorIs(t, err, test.want)
					}
					if test.raw != "" {
						var terminal *SandboxTerminalStateError
						require.ErrorAs(t, err, &terminal)
						require.Equal(t, test.raw, terminal.RawState)
						require.Equal(t, "observed reason", terminal.Reason)
						require.Equal(t, terminal.Reason, sandbox.Info().Error)
					}
					require.Equal(t, 2, reads)
				})
			})
		}
	}
}

func TestWaitRetry_whenReadTimeoutPrecedesWait_appliesPhasePolicy(t *testing.T) {
	for _, live := range []bool{true, false} {
		synctest.Test(t, func(t *testing.T) {
			// Given: one long-poll request times out while the unbounded wait still has budget.
			var reads []time.Time
			sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, r *http.Request) {
				reads = append(reads, time.Now())
				if len(reads) == 1 {
					<-r.Context().Done()
					return
				}
				writeJSON(t, w, sandboxFixture("sbx"))
			})
			source := &waitTestSource{events: make(chan stateObservation)}
			if !live {
				close(source.events)
			}
			sandbox.client.stateSource = source

			// When
			err := sandbox.Wait(t.Context(), &WaitOptions{Timeout: WaitForever})

			// Then: both an early probe and fallback polling retry a client read timeout.
			require.NoError(t, err)
			require.Len(t, reads, 2)
			require.GreaterOrEqual(t, reads[1].Sub(reads[0]), waitSlice+waitReadSlack+waitMinInterval)
			require.Equal(t, SandboxStateStarted, sandbox.State())
		})
	}
}

func TestWaitRetry_whenLaterReadsStall_retainsProbeFailure(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given: a connection reset followed by probes that use their whole remaining share.
		reads := 0
		sandbox := virtualWaitSandbox(t, func(_ http.ResponseWriter, r *http.Request) { <-r.Context().Done() })
		base := sandbox.client.http.Transport
		sandbox.client.http.Transport = waitRetryTransport(func(r *http.Request) (*http.Response, error) {
			reads++
			if reads == 1 {
				return nil, syscall.ECONNRESET
			}
			return base.RoundTrip(r)
		})
		sandbox.client.stateSource = &waitTestSource{events: make(chan stateObservation)}
		began := time.Now()

		// When
		err := sandbox.Wait(t.Context(), &WaitOptions{Timeout: 8 * time.Second})

		// Then: share/deadline bookkeeping must not replace the last actual read failure.
		require.ErrorIs(t, err, ErrRequestTimeout)
		require.ErrorIs(t, errors.Unwrap(err), syscall.ECONNRESET)
		require.Equal(t, 8*time.Second, time.Since(began))
		require.Equal(t, 2, reads) // The next live probe is beyond the share; only the reserve reads again.
	})
}
