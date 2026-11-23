// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"net/url"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestArtifactGet_whenWaitOptionsSelectTheRead(t *testing.T) {
	for _, resource := range imageArtifactResources() {
		for _, tt := range []struct {
			name  string
			opts  *GetArtifactOptions
			query url.Values
		}{
			{"ordinary", nil, url.Values{}},
			{"timeout without wait", &GetArtifactOptions{Timeout: time.Second}, url.Values{}},
			{"one hold", &GetArtifactOptions{Wait: true}, url.Values{"wait": {"ready"}}},
			{"fractional hold", &GetArtifactOptions{Wait: true, Timeout: 1250*time.Millisecond + time.Nanosecond}, url.Values{"wait": {"ready"}, "timeout_ms": {"1251"}}},
			{"capped hold", &GetArtifactOptions{Wait: true, Timeout: 2 * time.Minute}, url.Values{"wait": {"ready"}, "timeout_ms": {"60000"}}},
		} {
			t.Run(resource.name+"/"+tt.name, func(t *testing.T) {
				// Given a ready response and a shorter ordinary request timeout.
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"GET /" + resource.name + "/{id}": jsonHandler(t, resource.fixture("artifact/one", "ready")),
				})
				synctest.Test(t, func(t *testing.T) {
					client := imageClockClient(t, fake, time.Millisecond)
					// When getting with the chosen readiness options.
					status, err := resource.get(t.Context(), client, tt.opts)
					// Then query rounding and path escaping match the public contract.
					require.NoError(t, err)
					require.Equal(t, "ready", status)
					requests := fake.requests()
					require.Len(t, requests, 1)
					require.Equal(t, "/"+resource.name+"/artifact%2Fone", requests[0].Path)
					require.Equal(t, tt.query, requests[0].Query)
				})
			})
		}
	}
}

func TestArtifactGet_whenOneHoldReturnsUnsettled(t *testing.T) {
	for _, resource := range imageArtifactResources() {
		t.Run(resource.name, func(t *testing.T) {
			// Given a default hold that outlives the ordinary request timeout.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /" + resource.name + "/{id}": func(w http.ResponseWriter, r *http.Request) {
					if imageDelay(r.Context(), 2*time.Second) == nil {
						writeJSON(t, w, resource.fixture("artifact/one", "pending"))
					}
				},
			})
			synctest.Test(t, func(t *testing.T) {
				client := imageClockClient(t, fake, time.Second)
				// When waiting without a positive budget.
				status, err := resource.get(t.Context(), client, &GetArtifactOptions{Wait: true})
				// Then one nonterminal observation returns without a second hold.
				require.NoError(t, err)
				require.Equal(t, "pending", status)
				require.Len(t, fake.requests(), 1)
			})
		})
	}
}

func TestArtifactGet_whenEarlyAnswersRequireRepeatedHolds(t *testing.T) {
	for _, resource := range imageArtifactResources() {
		for _, budget := range []time.Duration{3 * time.Second, WaitForever, 3_000_000 * time.Second} {
			t.Run(resource.name+"/"+budget.String(), func(t *testing.T) {
				// Given two early nonterminal observations before readiness.
				var observations int
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"GET /" + resource.name + "/{id}": func(w http.ResponseWriter, _ *http.Request) {
						status := []string{"pending", "future-building", "ready"}[min(observations, 2)]
						observations++
						writeJSON(t, w, resource.fixture("artifact/one", status))
					},
				})
				synctest.Test(t, func(t *testing.T) {
					client := imageClockClient(t, fake, time.Millisecond)
					start := time.Now()
					// When waiting to settlement with a finite or unbounded budget.
					status, err := resource.get(t.Context(), client, &GetArtifactOptions{Wait: true, Timeout: budget})
					// Then neither intermediate result escapes and reads are paced.
					require.NoError(t, err)
					require.Equal(t, "ready", status)
					require.Equal(t, 2*time.Second, time.Since(start))
					requests := fake.requests()
					require.Len(t, requests, 3)
					holds := []string{"60000", "60000", "60000"}
					if budget == 3*time.Second {
						holds = []string{"3000", "2000", "1000"}
					}
					for index, request := range requests {
						require.Equal(t, "ready", request.Query.Get("wait"))
						require.Equal(t, holds[index], request.Query.Get("timeout_ms"))
					}
				})
			})
		}
	}
}

func TestArtifactGet_whenFailureSettlesTheWait(t *testing.T) {
	for _, resource := range imageArtifactResources() {
		for _, status := range []string{"failed", "error"} {
			t.Run(resource.name+"/"+status, func(t *testing.T) {
				// Given a terminal failed observation.
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"GET /" + resource.name + "/{id}": jsonHandler(t, resource.fixture("artifact/one", status)),
				})
				client := newTestClient(t, fake.Server)
				// When getting with a readiness wait.
				got, err := resource.get(t.Context(), client, &GetArtifactOptions{Wait: true, Timeout: time.Minute})
				// Then Get returns the failure observation for caller inspection.
				require.NoError(t, err)
				require.Equal(t, status, got)
				require.Len(t, fake.requests(), 1)
			})
		}
	}
}

func TestArtifactGet_whenCallerCancels(t *testing.T) {
	for _, resource := range imageArtifactResources() {
		t.Run(resource.name, func(t *testing.T) {
			// Given an active hold stopped by its caller.
			ctx, cancel := context.WithCancel(t.Context())
			defer cancel()
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /" + resource.name + "/{id}": func(_ http.ResponseWriter, _ *http.Request) { cancel() },
			})
			synctest.Test(t, func(t *testing.T) {
				client := imageClockClient(t, fake, time.Second)
				// When the hold is canceled rather than timed out.
				_, err := resource.get(ctx, client, &GetArtifactOptions{Wait: true, Timeout: time.Minute})
				// Then cancellation remains distinguishable from either timeout.
				require.ErrorIs(t, err, context.Canceled)
				require.NotErrorIs(t, err, ErrRequestTimeout)
				require.NotErrorIs(t, err, ErrClientTimeout)
				var detail *Error
				require.ErrorAs(t, err, &detail)
				require.Equal(t, SourceSDK, detail.Source)
			})
		})
	}
}
