// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"strconv"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestWaitRetry_whenEarlyProbeReturns400_keepsWaiting(t *testing.T) {
	for _, completion := range []string{"read", "event"} {
		t.Run(completion, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given: a live subscription and a nondecisive 400 from the first probe.
				source := &waitTestSource{events: make(chan stateObservation, 1)}
				firstRead := make(chan struct{})
				reads := 0
				sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, r *http.Request) {
					reads++
					if reads == 1 {
						w.WriteHeader(http.StatusBadRequest)
						close(firstRead)
						return
					}
					if completion == "event" && r.URL.RawQuery != "" {
						t.Error("target event must use a plain confirming read")
					}
					writeJSON(t, w, sandboxFixture("sbx"))
				})
				sandbox.client.stateSource = source

				// When
				done := make(chan error, 1)
				go func() { done <- sandbox.Wait(t.Context(), nil) }()
				if completion == "event" {
					<-firstRead
					synctest.Wait()
					source.events <- stateObservation{State: SandboxStateStarted, RawState: "running"}
				}
				err := <-done

				// Then: early-probe errors do not inherit fallback's permanent-4xx policy.
				require.NoError(t, err)
				require.Equal(t, SandboxStateStarted, sandbox.State())
				require.Equal(t, 2, reads)
			})
		})
	}
}

func TestWaitRetry_whenFinalReserveReturns400_keepsTimeoutCause(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given: the live hub uses its share without a probe; the final read fails permanently.
		reads := 0
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
			reads++
			w.WriteHeader(http.StatusBadRequest)
		})
		sandbox.client.stateSource = &waitTestSource{events: make(chan stateObservation), lastFrame: time.Now}

		// When
		err := sandbox.Wait(t.Context(), &WaitOptions{Timeout: time.Second})

		// Then: the reserve preserves timeout semantics without retrying a permanent error.
		var timeout *Error
		require.ErrorAs(t, err, &timeout)
		require.Equal(t, CodeRequestTimeout, timeout.Code)
		require.Equal(t, SourceSDK, timeout.Source)
		var cause *Error
		require.ErrorAs(t, timeout.Err, &cause)
		require.Equal(t, http.StatusBadRequest, cause.StatusCode)
		require.Equal(t, 1, reads)
	})
}

func TestWaitRetry_whenFallbackReturnsPermanent4xx_stopsImmediately(t *testing.T) {
	for _, mode := range []string{"closed", "unavailable"} {
		for _, status := range []int{400, 405, 408, 409, 410, 412, 413, 415, 422, 428} {
			t.Run(mode+"/"+strconv.Itoa(status), func(t *testing.T) {
				synctest.Test(t, func(t *testing.T) {
					// Given: enabled events with an unavailable subscription and a permanent read error.
					t.Setenv("DAYTONA_SANDBOX_EVENTS", "1")
					reads := 0
					sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
						reads++
						w.WriteHeader(status)
					})
					source := &waitTestSource{events: make(chan stateObservation)}
					if mode == "closed" {
						close(source.events)
					} else {
						source.events = nil
					}
					sandbox.client.stateSource = source
					began := time.Now()

					// When
					err := sandbox.Wait(t.Context(), nil)

					// Then: no backoff or retry turns a permanent 4xx into a wait timeout.
					var detail *Error
					require.ErrorAs(t, err, &detail)
					require.Equal(t, status, detail.StatusCode)
					require.Equal(t, 1, reads)
					require.Zero(t, time.Since(began))
				})
			})
		}
	}
}

func TestWaitRetry_whenProbeTransitionsToFallback_usesPermanent4xxPolicy(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given: a 400 is retried as a live probe, then the subscription closes.
		firstRead := make(chan struct{})
		source := &waitTestSource{events: make(chan stateObservation)}
		reads := 0
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
			reads++
			w.WriteHeader(http.StatusBadRequest)
			if reads == 1 {
				close(firstRead)
			}
		})
		sandbox.client.stateSource = source
		done := make(chan error, 1)
		go func() { done <- sandbox.Wait(t.Context(), nil) }()
		<-firstRead
		synctest.Wait()
		began := time.Now()

		// When
		close(source.events)
		err := <-done

		// Then: the outstanding backoff is honored, but the next 400 ends fallback.
		var detail *Error
		require.ErrorAs(t, err, &detail)
		require.Equal(t, http.StatusBadRequest, detail.StatusCode)
		require.Equal(t, 2, reads)
		require.Equal(t, waitMinInterval, time.Since(began))
	})
}
