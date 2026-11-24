// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestEventsConnectHonorsRetryAfterWhenMintOrUpgradeIsThrottled(t *testing.T) {
	for _, stage := range []string{"mint", "upgrade"} {
		t.Run(stage, func(t *testing.T) {
			// Given
			throttle := func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Retry-After", "12")
				w.WriteHeader(http.StatusTooManyRequests)
			}
			mint := jsonHandler(t, map[string]string{"ticket": "test-ticket"})
			if stage == "mint" {
				mint = throttle
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /events/a/ticket": mint, "GET /events/a": throttle,
			})
			client := newEventsTestClient(t, fake.Server)

			// When
			result, err := client.connectSandboxEvents(t.Context(), "a")

			// Then
			require.ErrorIs(t, err, ErrRateLimit)
			require.Equal(t, 12*time.Second, result.RetryAfter)
			require.False(t, result.Permanent)
		})
	}
}

func TestEventsUpgradePermanentlyDisablesWhenWorkloadPlaneElsewhere(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /events/a/ticket": jsonHandler(t, map[string]string{"ticket": "test-ticket"}),
		"GET /events/a": func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusNotImplemented)
			writeJSON(t, w, map[string]string{"code": CodeWorkloadPlaneElsewhere, "source": SourceAPI})
		},
	})
	client := newEventsTestClient(t, fake.Server)

	// When
	result, err := client.connectSandboxEvents(t.Context(), "a")

	// Then
	require.ErrorIs(t, err, ErrWorkloadPlaneElsewhere)
	require.True(t, result.Permanent)
}

func TestEventsUpgradeRefusesRedirectWhenServerSuppliesLocation(t *testing.T) {
	// Given
	target := newFakeServer(t, nil)
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /events/a/ticket": jsonHandler(t, map[string]string{"ticket": "test-ticket"}),
		"GET /events/a": func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, target.Server.URL, http.StatusTemporaryRedirect)
		},
	})
	client := newEventsTestClient(t, fake.Server)

	// When
	_, err := client.connectSandboxEvents(t.Context(), "a")

	// Then
	var detail *Error
	require.ErrorAs(t, err, &detail)
	require.Equal(t, http.StatusTemporaryRedirect, detail.StatusCode)
	require.Empty(t, target.requests())
}

func TestEventsCloseCancelsMintWhenRequestIsBlocked(t *testing.T) {
	// Given
	entered, exited := make(chan struct{}), make(chan struct{})
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /events/a/ticket": func(_ http.ResponseWriter, r *http.Request) {
			close(entered)
			<-r.Context().Done()
			close(exited)
		},
	})
	client := newEventsTestClient(t, fake.Server)
	ch, release, _ := client.stateSource.watch(t.Context(), "bare", "a")
	defer release()
	<-entered

	// When
	require.NoError(t, client.Close())

	// Then
	select {
	case _, ok := <-ch:
		require.False(t, ok)
	case <-time.After(5 * time.Second):
		t.Fatal("Close left a watch open")
	}
	<-exited
}

func TestEventsWatchCancelInterruptsMintWhenLastWatchLeaves(t *testing.T) {
	// Given
	entered, exited := make(chan struct{}), make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		close(entered)
		<-r.Context().Done()
		close(exited)
	}))
	defer server.Close()
	client := newEventsTestClient(t, server)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	_, release, _ := client.stateSource.watch(ctx, "bare", "a")
	defer release()
	<-entered

	// When
	cancel()

	// Then
	select {
	case <-exited:
	case <-time.After(5 * time.Second):
		t.Fatal("last watch left the ticket request running")
	}
}
