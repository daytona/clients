// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestImagePull_whenOrdinaryRequestTimesOutFirst(t *testing.T) {
	for _, phase := range []string{"submit", "lookup"} {
		t.Run(phase, func(t *testing.T) {
			// Given a request timer shorter than the total pull budget.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /images": func(w http.ResponseWriter, r *http.Request) {
					if phase == "submit" {
						<-r.Context().Done()
						return
					}
					writeJSON(t, w, map[string]string{"id": "img"})
				},
				"GET /images/img": func(_ http.ResponseWriter, r *http.Request) { <-r.Context().Done() },
			})
			synctest.Test(t, func(t *testing.T) {
				client := imageClockClient(t, fake, time.Second)
				start := time.Now()
				// When the submission or initial lookup stalls (a pull that waits
				// retries its lookup, so this one does not wait).
				noWait := false
				_, err := client.Images().Pull(t.Context(), "alpine", &PullImageOptions{Timeout: 10 * time.Second, Wait: &noWait})
				// Then the root timer retains its category rather than becoming a pull timeout.
				require.ErrorIs(t, err, ErrClientTimeout)
				require.ErrorIs(t, err, context.DeadlineExceeded)
				require.Equal(t, time.Second, time.Since(start))
			})
		})
	}
}

func TestImagePull_whenHoldOutlivesOrdinaryTimeout(t *testing.T) {
	// Given immediate submission, then a readiness hold longer than the root timeout.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /images": jsonHandler(t, map[string]string{"id": "img"}),
		"GET /images/img": func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Query().Has("wait") {
				if imageDelay(r.Context(), 2*time.Second) == nil {
					writeJSON(t, w, imageFixture("img", "ready"))
				}
				return
			}
			writeJSON(t, w, imageFixture("img", "pending"))
		},
	})
	synctest.Test(t, func(t *testing.T) {
		client := imageClockClient(t, fake, time.Second)
		// When polling within a longer total budget.
		image, err := client.Images().Pull(t.Context(), "alpine", &PullImageOptions{Timeout: 3 * time.Second})
		// Then only the remaining pull budget governs the long poll.
		require.NoError(t, err)
		require.Equal(t, ImageStatusReady, image.Status)
		requests := fake.requests()
		require.Len(t, requests, 3)
		require.Equal(t, "3000", requests[2].Query.Get("timeout_ms"))
	})
}

func TestImagePull_whenServerReturnsTimeoutBeforeDeadline(t *testing.T) {
	// Given a server-generated 408 from the readiness hold.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /images": jsonHandler(t, map[string]string{"id": "img"}),
		"GET /images/img": func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Query().Has("wait") {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(408)
				writeJSON(t, w, map[string]string{"code": "REQUEST_TIMEOUT", "source": "API"})
				return
			}
			writeJSON(t, w, imageFixture("img", "pending"))
		},
	})
	client := newTestClient(t, fake.Server)
	// When pulling within a still-active budget.
	_, err := client.Images().Pull(t.Context(), "alpine", &PullImageOptions{Timeout: time.Minute})
	// Then the API timeout keeps its provenance.
	require.ErrorIs(t, err, ErrRequestTimeout)
	require.NotErrorIs(t, err, ErrClientTimeout)
	require.NotErrorIs(t, err, context.DeadlineExceeded)
	var detail *Error
	require.ErrorAs(t, err, &detail)
	require.Equal(t, "API", detail.Source)
}

func TestImagePull_whenCallerCancelsReadiness(t *testing.T) {
	// Given an active readiness request whose caller cancels it.
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /images": jsonHandler(t, map[string]string{"id": "img"}),
		"GET /images/img": func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Query().Has("wait") {
				cancel()
				return
			}
			writeJSON(t, w, imageFixture("img", "pending"))
		},
	})
	synctest.Test(t, func(t *testing.T) {
		client := imageClockClient(t, fake, time.Second)
		// When waiting to readiness.
		_, err := client.Images().Pull(ctx, "alpine", &PullImageOptions{Timeout: time.Minute})
		// Then caller cancellation is preserved rather than becoming a build failure.
		require.ErrorIs(t, err, context.Canceled)
		require.NotErrorIs(t, err, ErrRequestTimeout)
		require.NotErrorIs(t, err, ErrImageBuildFailed)
	})
}

func TestImagePull_whenCumulativeWorkReachesDeadlineDuringPoll(t *testing.T) {
	// Given three phases whose individual durations fit but whose sum exceeds one second.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /images": func(w http.ResponseWriter, r *http.Request) {
			if imageDelay(r.Context(), 400*time.Millisecond) == nil {
				writeJSON(t, w, map[string]string{"id": "img"})
			}
		},
		"GET /images/img": func(w http.ResponseWriter, r *http.Request) {
			if imageDelay(r.Context(), 400*time.Millisecond) != nil {
				return
			}
			status := "pending"
			if r.URL.Query().Has("wait") {
				status = "ready"
			}
			writeJSON(t, w, imageFixture("img", status))
		},
	})
	synctest.Test(t, func(t *testing.T) {
		client := imageClockClient(t, fake, 10*time.Second)
		start := time.Now()
		// When pulling with one shared deadline.
		_, err := client.Images().Pull(t.Context(), "alpine", &PullImageOptions{Timeout: time.Second})
		// Then the poll only receives the unspent 200 ms.
		requireImageTimeout(t, err)
		require.Equal(t, time.Second, time.Since(start))
		requests := fake.requests()
		require.Len(t, requests, 3)
		require.Equal(t, "200", requests[2].Query.Get("timeout_ms"))
	})
}
