// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"net/http"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestWaitRetry_whenRepeatedFailures_backoffGrows(t *testing.T) {
	for _, live := range []bool{true, false} {
		synctest.Test(t, func(t *testing.T) {
			// Given: three immediate gateway errors before a successful read.
			var reads []time.Time
			sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
				reads = append(reads, time.Now())
				if len(reads) <= 3 {
					w.WriteHeader(http.StatusBadGateway)
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
			err := sandbox.Wait(t.Context(), nil)

			// Then: repeated failures cannot create a tight polling loop.
			require.NoError(t, err)
			require.Len(t, reads, 4)
			previous := time.Duration(0)
			for i := 1; i < len(reads); i++ {
				gap := reads[i].Sub(reads[i-1])
				require.GreaterOrEqual(t, gap, waitMinInterval)
				if live {
					require.Equal(t, 10*time.Second, gap)
				} else {
					require.Greater(t, gap, previous)
				}
				previous = gap
			}
		})
	}
}

func TestWaitRetry_whenBudgetExpires_retainsLastReadError(t *testing.T) {
	for _, live := range []bool{true, false} {
		for _, recoverRead := range []bool{true, false} {
			synctest.Test(t, func(t *testing.T) {
				// Given: the last failed read differs from the first; later reads may recover.
				reads := 0
				sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
					reads++
					switch {
					case reads == 1:
						w.WriteHeader(http.StatusBadGateway)
					case reads == 2 || !recoverRead:
						w.WriteHeader(http.StatusServiceUnavailable)
					default:
						detail := sandboxFixture("sbx")
						detail["status"] = "booting"
						writeJSON(t, w, detail)
					}
				})
				source := &waitTestSource{events: make(chan stateObservation)}
				if !live {
					close(source.events)
				}
				sandbox.client.stateSource = source
				began := time.Now()

				// When
				err := sandbox.Wait(t.Context(), &WaitOptions{Timeout: 8 * time.Second})

				// Then: even across the final reserve, the wait spends its budget and keeps the last failure.
				require.ErrorIs(t, err, ErrRequestTimeout)
				require.Equal(t, 8*time.Second, time.Since(began))
				var cause *Error
				require.ErrorAs(t, errors.Unwrap(err), &cause)
				require.Equal(t, http.StatusServiceUnavailable, cause.StatusCode)
			})
		}
	}
}

func TestWaitRetry_whenCallerEndsBackoff_stopsImmediately(t *testing.T) {
	for _, mode := range []string{"live", "closed"} {
		for _, ending := range []string{"cancel", "close"} {
			t.Run(mode+"/"+ending, func(t *testing.T) {
				synctest.Test(t, func(t *testing.T) {
					// Given: a failed read and a wait blocked in retry backoff.
					read := make(chan struct{}, 1)
					sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
						read <- struct{}{}
						w.WriteHeader(http.StatusServiceUnavailable)
					})
					source := &waitTestSource{events: make(chan stateObservation)}
					if mode == "closed" {
						close(source.events)
					}
					sandbox.client.stateSource = source
					ctx, cancel := context.WithCancel(t.Context())
					defer cancel()
					done := make(chan error, 1)
					go func() { done <- sandbox.Wait(ctx, &WaitOptions{Timeout: WaitForever}) }()
					<-read
					synctest.Wait()
					began := time.Now()

					// When
					want := context.Canceled
					switch ending {
					case "cancel":
						cancel()
					case "close":
						want = ErrClientClosed
						require.NoError(t, sandbox.client.Close())
					}
					err := <-done

					// Then
					require.ErrorIs(t, err, want)
					require.Less(t, time.Since(began), waitMinInterval)
					require.Empty(t, read)
					require.EqualValues(t, 1, source.released.Load())
				})
			})
		}
	}
}

func TestWaitRetry_whenCallerDeadlineExpires_preservesCause(t *testing.T) {
	for _, live := range []bool{true, false} {
		synctest.Test(t, func(t *testing.T) {
			// Given: repeated read failures within a caller-bounded unbounded wait.
			sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(http.StatusServiceUnavailable)
			})
			source := &waitTestSource{events: make(chan stateObservation)}
			if !live {
				close(source.events)
			}
			sandbox.client.stateSource = source
			ctx, cancel := context.WithTimeout(t.Context(), 8*time.Second)
			defer cancel()
			began := time.Now()

			// When
			err := sandbox.Wait(ctx, &WaitOptions{Timeout: WaitForever})

			// Then: a prior read error cannot replace the caller's deadline.
			require.ErrorIs(t, err, context.DeadlineExceeded)
			require.Equal(t, 8*time.Second, time.Since(began))
		})
	}
}

func TestWaitRetry_whenNotFoundArrivesDuringBackoff_keepsListening(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given: the first probe failed, but the events subscription remains live.
		read := make(chan struct{}, 1)
		sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
			read <- struct{}{}
			w.WriteHeader(http.StatusBadGateway)
		})
		source := &waitTestSource{events: make(chan stateObservation, 1)}
		sandbox.client.stateSource = source
		done := make(chan error, 1)
		go func() { done <- sandbox.Wait(t.Context(), nil) }()
		<-read
		synctest.Wait()
		began := time.Now()

		// When
		source.events <- stateObservation{NotFound: true}
		err := <-done

		// Then: a decisive event settles without waiting for or issuing another read.
		require.ErrorIs(t, err, ErrNotFound)
		require.Zero(t, time.Since(began))
		require.Empty(t, read)
		require.EqualValues(t, 1, source.released.Load())
	})
}
