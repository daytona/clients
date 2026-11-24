// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"sync/atomic"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestAccountLimitsExplicitOrganizationWithJWT(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /auth/token":        jsonHandler(t, map[string]any{"access_token": "project-token", "expires_in": 300}),
		"GET /orgs/org-id/limits": jsonHandler(t, accountLimitsFixture()),
	})
	client := newTestClient(t, fake.Server, WithJWTToken("jwt"), WithProjectID("project-id"))
	// When
	_, err := client.Limits(t.Context(), &GetLimitsOptions{OrganizationID: "org-id"})
	// Then
	require.NoError(t, err)
	requests := fake.requests()
	require.Len(t, requests, 2)
	require.Equal(t, "/auth/token", requests[0].Path)
	require.Equal(t, "/orgs/org-id/limits", requests[1].Path)
	require.Equal(t, "Bearer project-token", requests[1].Header.Get("Authorization"))
}

func TestAccountLimitsLookupCacheBelongsToEachClient(t *testing.T) {
	// Given
	var lookups atomic.Int64
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /api-keys/current": func(w http.ResponseWriter, _ *http.Request) {
			lookups.Add(1)
			writeJSON(t, w, accountKeyFixture())
		},
		"GET /orgs/org-id/limits": jsonHandler(t, accountLimitsFixture()),
		"GET /orgs/other/limits":  jsonHandler(t, accountLimitsFixture()),
	})
	first := newTestClient(t, fake.Server)
	second := newTestClient(t, fake.Server)
	_, setupErr := first.Limits(t.Context(), nil)
	require.NoError(t, setupErr)
	_, setupErr = first.Limits(t.Context(), &GetLimitsOptions{OrganizationID: "other"})
	require.NoError(t, setupErr)
	_, setupErr = first.Limits(t.Context(), nil)
	require.NoError(t, setupErr)
	// When
	_, err := second.Limits(t.Context(), nil)
	// Then
	require.NoError(t, err)
	require.EqualValues(t, 2, lookups.Load())
	requests := fake.requests()
	require.Len(t, requests, 6)
	require.Equal(t, "/orgs/other/limits", requests[2].Path)
	require.Equal(t, "/orgs/org-id/limits", requests[3].Path)
}

func TestAccountLimitsCompletesLookupAfterFirstCallerCancels(t *testing.T) {
	// Given
	started, release := make(chan struct{}), make(chan struct{})
	var lookups atomic.Int64
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /api-keys/current": func(w http.ResponseWriter, r *http.Request) {
			if lookups.Add(1) == 1 {
				close(started)
				select {
				case <-release:
				case <-r.Context().Done():
					return
				}
			}
			writeJSON(t, w, accountKeyFixture())
		},
		"GET /orgs/org-id/limits": jsonHandler(t, accountLimitsFixture()),
	})
	client := newTestClient(t, fake.Server)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	first := make(chan error, 1)
	go func() {
		_, firstErr := client.Limits(ctx, nil)
		first <- firstErr
	}()
	<-started
	cancel()
	require.ErrorIs(t, <-first, context.Canceled)
	// When
	close(release)
	_, err := client.Limits(t.Context(), nil)
	// Then
	require.NoError(t, err)
	require.EqualValues(t, 1, lookups.Load())
}

func TestAccountLimitsCloseCancelsLookupAndWaiter(t *testing.T) {
	// Given
	started := make(chan struct{})
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /api-keys/current": func(_ http.ResponseWriter, r *http.Request) {
			close(started)
			<-r.Context().Done()
		},
	})
	client := newTestClient(t, fake.Server)
	first := make(chan error, 1)
	go func() {
		_, err := client.Limits(t.Context(), nil)
		first <- err
	}()
	<-started
	waiting := &accountObservedContext{Context: t.Context(), selected: make(chan struct{})}
	second := make(chan error, 1)
	go func() {
		_, err := client.Limits(waiting, nil)
		second <- err
	}()
	<-waiting.selected
	// When
	err := client.Close()
	// Then
	require.NoError(t, err)
	require.ErrorIs(t, <-first, ErrClientClosed)
	require.ErrorIs(t, <-second, ErrClientClosed)
	require.Len(t, fake.requests(), 1)
}
