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

func TestArtifactGet_whenReadExhaustsOwnBudget(t *testing.T) {
	for _, resource := range imageArtifactResources() {
		t.Run(resource.name, func(t *testing.T) {
			// Given a stalled server hold and a shorter ordinary request timeout.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /" + resource.name + "/{id}": func(_ http.ResponseWriter, r *http.Request) {
					<-r.Context().Done()
				},
			})
			synctest.Test(t, func(t *testing.T) {
				client := imageClockClient(t, fake, 100*time.Millisecond)
				start := time.Now()
				// When the readiness budget expires while reading.
				_, err := resource.get(t.Context(), client, &GetArtifactOptions{Wait: true, Timeout: time.Second})
				// Then the operation owns the timeout, never the root request timer.
				requireImageTimeout(t, err)
				require.Equal(t, time.Second, time.Since(start))
				require.Len(t, fake.requests(), 1)
			})
		})
	}
}

func TestArtifactGet_whenBudgetExpiresBetweenReads(t *testing.T) {
	for _, resource := range imageArtifactResources() {
		t.Run(resource.name, func(t *testing.T) {
			// Given a server repeatedly answering pending immediately.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /" + resource.name + "/{id}": jsonHandler(t, resource.fixture("artifact/one", "pending")),
			})
			synctest.Test(t, func(t *testing.T) {
				client := imageClockClient(t, fake, time.Minute)
				start := time.Now()
				// When the budget expires in the pacing pause.
				_, err := resource.get(t.Context(), client, &GetArtifactOptions{Wait: true, Timeout: 1500 * time.Millisecond})
				// Then no unsettled observation escapes and the deadline is shared.
				requireImageTimeout(t, err)
				require.Equal(t, 1500*time.Millisecond, time.Since(start))
				requests := fake.requests()
				require.Len(t, requests, 2)
				require.Equal(t, "1500", requests[0].Query.Get("timeout_ms"))
				require.Equal(t, "500", requests[1].Query.Get("timeout_ms"))
			})
		})
	}
}

func TestArtifactGet_whenServerReturnsItsOwnTimeout(t *testing.T) {
	for _, resource := range imageArtifactResources() {
		t.Run(resource.name, func(t *testing.T) {
			// Given an HTTP 408 received while the readiness budget remains.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /" + resource.name + "/{id}": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(408)
					writeJSON(t, w, map[string]string{"code": "REQUEST_TIMEOUT", "source": "API", "message": "server timeout"})
				},
			})
			client := newTestClient(t, fake.Server)
			// When waiting for readiness.
			_, err := resource.get(t.Context(), client, &GetArtifactOptions{Wait: true, Timeout: time.Minute})
			// Then a server timeout is not rewritten as SDK budget exhaustion.
			require.ErrorIs(t, err, ErrRequestTimeout)
			require.NotErrorIs(t, err, ErrClientTimeout)
			require.NotErrorIs(t, err, context.DeadlineExceeded)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, "API", detail.Source)
			require.Equal(t, "server timeout", detail.Message)
		})
	}
}

func TestArtifactGet_whenWaitIsFalseKeepsRequestTimeout(t *testing.T) {
	for _, resource := range imageArtifactResources() {
		t.Run(resource.name, func(t *testing.T) {
			// Given an ordinary read that stalls despite a larger unused wait budget.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /" + resource.name + "/{id}": func(_ http.ResponseWriter, r *http.Request) {
					<-r.Context().Done()
				},
			})
			synctest.Test(t, func(t *testing.T) {
				client := imageClockClient(t, fake, time.Second)
				start := time.Now()
				// When reading without Wait.
				_, err := resource.get(t.Context(), client, &GetArtifactOptions{Timeout: time.Minute})
				// Then the ordinary request deadline remains in effect.
				require.ErrorIs(t, err, ErrClientTimeout)
				require.ErrorIs(t, err, context.DeadlineExceeded)
				require.Equal(t, time.Second, time.Since(start))
			})
		})
	}
}

func TestArtifactGet_whenCanceledBeforeWaiting(t *testing.T) {
	for _, resource := range imageArtifactResources() {
		t.Run(resource.name, func(t *testing.T) {
			// Given an already-canceled caller and no network expectation.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			ctx, cancel := context.WithCancel(t.Context())
			cancel()
			// When starting a readiness wait.
			_, err := resource.get(ctx, client, &GetArtifactOptions{Wait: true, Timeout: time.Minute})
			// Then cancellation retains the SDK error hierarchy without HTTP.
			require.ErrorIs(t, err, context.Canceled)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, SourceSDK, detail.Source)
			require.Empty(t, fake.requests())
		})
	}
}
