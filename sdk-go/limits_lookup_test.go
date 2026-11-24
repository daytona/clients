// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"sync/atomic"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestAccountLimitsLiveWaiterSurvivesFirstCallerCancellation(t *testing.T) {
	// Given a shared lookup held until both callers are waiting.
	started, release := make(chan struct{}), make(chan struct{})
	var lookups atomic.Int64
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /api-keys/current": func(w http.ResponseWriter, r *http.Request) {
			if lookups.Add(1) == 1 {
				close(started)
			}
			select {
			case <-release:
				writeJSON(t, w, accountKeyFixture())
			case <-r.Context().Done():
			}
		},
		"GET /orgs/org-id/limits": jsonHandler(t, accountLimitsFixture()),
	})
	client := newTestClient(t, fake.Server)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	first := make(chan error, 1)
	go func() {
		_, err := client.Limits(ctx, nil)
		first <- err
	}()
	<-started
	waiting := &accountObservedContext{Context: t.Context(), selected: make(chan struct{})}
	second := make(chan error, 1)
	go func() {
		limits, err := client.Limits(waiting, nil)
		if err == nil && limits.Sandboxes.Concurrent.Usage != 7 {
			t.Error("live waiter received the wrong limits")
		}
		second <- err
	}()
	<-waiting.selected
	// When the first caller cancels before the lookup completes.
	cancel()
	require.ErrorIs(t, <-first, context.Canceled)
	close(release)
	// Then the live waiter receives the result from the same lookup.
	require.NoError(t, <-second)
	require.EqualValues(t, 1, lookups.Load())
}

func TestAccountLimitsRetriesEmptyOrganizationID(t *testing.T) {
	// Given an initially empty organization followed by a valid response.
	var lookups atomic.Int64
	fixture := accountKeyFixture()
	fixture["context"] = map[string]string{"org_id": "", "org_name": "Organization", "project_name": "Project"}
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /api-keys/current": func(w http.ResponseWriter, _ *http.Request) {
			if lookups.Add(1) == 1 {
				writeJSON(t, w, fixture)
				return
			}
			writeJSON(t, w, accountKeyFixture())
		},
		"GET /orgs/org-id/limits": jsonHandler(t, accountLimitsFixture()),
	})
	client := newTestClient(t, fake.Server)
	_, firstErr := client.Limits(t.Context(), nil)
	require.ErrorIs(t, firstErr, ErrBadRequest)
	// When a later caller asks for the limits.
	result, err := client.Limits(t.Context(), nil)
	// Then the empty ID did not poison the cache.
	require.NoError(t, err)
	require.EqualValues(t, 7, result.Sandboxes.Concurrent.Usage)
	require.EqualValues(t, 2, lookups.Load())
}

func TestAccountLimitsSharedLookupKeepsRequestTimeout(t *testing.T) {
	// Given a stalled HTTP exchange on a virtual clock.
	fake := newFakeServer(t, nil)
	synctest.Test(t, func(t *testing.T) {
		started := make(chan struct{})
		transport := buildLogsRoundTrip(func(request *http.Request) (*http.Response, error) {
			close(started)
			<-request.Context().Done()
			return nil, request.Context().Err()
		})
		client := newTestClient(t, fake.Server, WithRequestTimeout(time.Second),
			WithHTTPClient(&http.Client{Transport: transport}))
		ctx, cancel := context.WithCancel(t.Context())
		defer cancel()
		first := make(chan error, 1)
		go func() {
			_, err := client.Limits(ctx, nil)
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
		cancel()
		require.ErrorIs(t, <-first, context.Canceled)
		// When the shared request's own budget expires.
		err := <-second
		// Then the live caller receives a request timeout, not the cancellation.
		require.ErrorIs(t, err, ErrClientTimeout)
		require.ErrorIs(t, err, context.DeadlineExceeded)
	})
}
