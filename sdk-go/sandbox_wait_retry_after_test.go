// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"strconv"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestWaitRetry_whenRateLimited_honorsRetryAfter(t *testing.T) {
	for _, mode := range []string{"live", "closed", "unavailable", "closes_during_backoff"} {
		for _, hint := range []int{0, 2} {
			t.Run(mode+"/"+strconv.Itoa(hint), func(t *testing.T) {
				synctest.Test(t, func(t *testing.T) {
					// Given: two rate-limited reads with a retry hint before reaching started.
					t.Setenv("DAYTONA_SANDBOX_EVENTS", "1")
					var reads []time.Time
					firstRead := make(chan struct{})
					source := &waitTestSource{events: make(chan stateObservation)}
					sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
						reads = append(reads, time.Now())
						if len(reads) <= 2 {
							w.Header().Set("Retry-After", strconv.Itoa(hint))
							w.WriteHeader(http.StatusTooManyRequests)
							if len(reads) == 1 {
								close(firstRead)
							}
							return
						}
						writeJSON(t, w, sandboxFixture("sbx"))
					})
					switch mode {
					case "closed":
						close(source.events)
					case "unavailable":
						source.events = nil
					}
					sandbox.client.stateSource = source

					// When
					done := make(chan error, 1)
					go func() { done <- sandbox.Wait(t.Context(), nil) }()
					if mode == "closes_during_backoff" {
						<-firstRead
						synctest.Wait()
						close(source.events)
					}
					err := <-done

					// Then: a source closing cannot skip the outstanding server retry delay.
					require.NoError(t, err)
					require.Len(t, reads, 3)
					for i := 1; i < len(reads); i++ {
						gap := reads[i].Sub(reads[i-1])
						require.GreaterOrEqual(t, gap, max(waitMinInterval, time.Duration(hint)*time.Second))
					}
					if hint == 0 {
						require.Greater(t, reads[2].Sub(reads[1]), reads[1].Sub(reads[0]))
					}
				})
			})
		}
	}
}

func TestWaitRetry_whenRetryAfterExceedsBudget_stopsAtDeadline(t *testing.T) {
	for _, live := range []bool{true, false} {
		for _, callerDeadline := range []bool{true, false} {
			synctest.Test(t, func(t *testing.T) {
				// Given: the server's retry hint exceeds both the hub share and the whole wait.
				reads := 0
				sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
					reads++
					w.Header().Set("Retry-After", "60")
					w.WriteHeader(http.StatusTooManyRequests)
				})
				source := &waitTestSource{events: make(chan stateObservation)}
				if !live {
					close(source.events)
				}
				sandbox.client.stateSource = source
				ctx, cancel := context.WithCancel(t.Context())
				defer cancel()
				if callerDeadline {
					var stop context.CancelFunc
					ctx, stop = context.WithTimeout(ctx, 8*time.Second)
					defer stop()
				}
				began := time.Now()

				// When
				err := sandbox.Wait(ctx, &WaitOptions{Timeout: 8 * time.Second})

				// Then: the wait deadline bounds RetryAfter; the server error is kept only as the SDK timeout's cause.
				require.Equal(t, 8*time.Second, time.Since(began))
				require.Equal(t, 1, reads)
				if callerDeadline {
					require.ErrorIs(t, err, context.DeadlineExceeded)
				} else {
					var timeout *Error
					require.ErrorAs(t, err, &timeout)
					require.Equal(t, CodeRequestTimeout, timeout.Code)
					var cause *Error
					require.ErrorAs(t, timeout.Err, &cause)
					require.Equal(t, http.StatusTooManyRequests, cause.StatusCode)
					require.Equal(t, time.Minute, cause.RetryAfter)
				}
			})
		}
	}
}
