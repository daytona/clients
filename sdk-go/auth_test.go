// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestAuth_whenConcurrentCallsRejectSameToken(t *testing.T) {
	// Given
	const callers = 12
	var exchanges, oldRequests atomic.Int32
	allOld := make(chan struct{})
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /auth/token": func(w http.ResponseWriter, _ *http.Request) {
			token := "old"
			if exchanges.Add(1) > 1 {
				token = "fresh"
			}
			writeJSON(t, w, map[string]any{"access_token": token, "expires_in": 300})
		},
		"GET /protected": func(w http.ResponseWriter, r *http.Request) {
			if r.Header.Get("Authorization") == "Bearer old" {
				if oldRequests.Add(1) == callers {
					close(allOld)
				}
				<-allOld
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(401)
			}
			writeJSON(t, w, struct{}{})
		},
	})
	client := newTestClient(t, fake.Server, WithJWTToken("user-jwt"), WithProjectID("project-one"))
	failures := make(chan error, callers)
	var workers sync.WaitGroup
	// When
	for range callers {
		workers.Go(func() { failures <- client.api().do(t.Context(), apiRequest{Path: "/protected"}) })
	}
	workers.Wait()
	close(failures)
	// Then
	for err := range failures {
		require.NoError(t, err)
	}
	require.EqualValues(t, 2, exchanges.Load())
	exchange := fake.requests()[0]
	require.Equal(t, "Bearer user-jwt", exchange.Header.Get("Authorization"))
	require.JSONEq(t, `{
		"grant_type":"urn:ietf:params:oauth:grant-type:token-exchange",
		"subject_token":"user-jwt","subject_token_type":"urn:ietf:params:oauth:token-type:jwt","project_id":"project-one"
	}`, string(exchange.Body))
}

func TestAuth_whenShortTokenExpires(t *testing.T) {
	// Given
	var exchanges atomic.Int32
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /auth/token": func(w http.ResponseWriter, _ *http.Request) {
			exchanges.Add(1)
			writeJSON(t, w, map[string]any{"access_token": "short-lived", "expires_in": 10})
		},
		"GET /": jsonHandler(t, struct{}{}),
	})
	client := newTestClient(t, fake.Server, WithJWTToken("jwt"), WithProjectID("p"))
	provider, ok := client.auth.(*projectCredential)
	require.True(t, ok)
	now := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	provider.cache.now = func() time.Time { return now }
	require.NoError(t, client.api().do(t.Context(), apiRequest{Path: "/"}))
	require.NoError(t, client.api().do(t.Context(), apiRequest{Path: "/"}))
	require.EqualValues(t, 1, exchanges.Load())
	// When
	now = now.Add(5 * time.Second)
	require.NoError(t, client.api().do(t.Context(), apiRequest{Path: "/"}))
	// Then
	require.EqualValues(t, 2, exchanges.Load())
}

func TestAuth_whenExchangeIsMalformed(t *testing.T) {
	for _, body := range []string{
		`{}`, `{"access_token":"","expires_in":1}`, `{"access_token":"x","expires_in":0}`,
		`{"access_token":"x","expires_in":true}`, `{"access_token":"x","expires_in":1.5}`,
	} {
		t.Run(body, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /auth/token": jsonHandler(t, JSONValue(body)),
			})
			client := newTestClient(t, fake.Server, WithJWTToken("jwt"), WithProjectID("p"))
			// When
			err := client.api().do(t.Context(), apiRequest{Path: "/protected"})
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestAuth_whenWaiterCancelsSharedExchangeContinues(t *testing.T) {
	// Given
	entered, release := make(chan struct{}), make(chan struct{})
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /auth/token": func(w http.ResponseWriter, r *http.Request) {
			close(entered)
			select {
			case <-release:
			case <-r.Context().Done():
				return
			}
			writeJSON(t, w, map[string]any{"access_token": "shared", "expires_in": 300})
		},
		"GET /": jsonHandler(t, struct{}{}),
	})
	client := newTestClient(t, fake.Server, WithJWTToken("jwt"), WithProjectID("p"))
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- client.api().do(ctx, apiRequest{Path: "/"}) }()
	<-entered
	// When
	cancel()
	require.ErrorIs(t, <-done, context.Canceled)
	close(release)
	err := client.api().do(t.Context(), apiRequest{Path: "/"})
	// Then
	require.NoError(t, err)
	require.Len(t, fake.requests(), 2)
}

func TestAuth_whenExchangeOutlivesPerCallBudget(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /auth/token": func(_ http.ResponseWriter, r *http.Request) {
		<-r.Context().Done()
	}})
	client := newTestClient(t, fake.Server, WithJWTToken("jwt"), WithProjectID("p"), WithRequestTimeout(20*time.Millisecond))
	// When
	err := client.api().do(t.Context(), apiRequest{Path: "/", Timeout: time.Second})
	// Then
	require.ErrorIs(t, err, ErrClientTimeout)
	require.Len(t, fake.requests(), 1)
}
