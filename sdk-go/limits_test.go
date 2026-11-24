// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"net/url"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestAccountLimitsEscapesOrganizationID(t *testing.T) {
	// Given
	id := "org/with ?#"
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /orgs/{organization}/limits": jsonHandler(t, accountLimitsFixture()),
	})
	client := newTestClient(t, fake.Server)
	// When
	_, err := client.Limits(t.Context(), &GetLimitsOptions{OrganizationID: id})
	// Then
	require.NoError(t, err)
	requests := fake.requests()
	require.Len(t, requests, 1)
	require.Equal(t, "/orgs/"+url.PathEscape(id)+"/limits", requests[0].Path)
	require.Empty(t, requests[0].Query)
}

func TestAccountLimitsRejectsInvalidOrganizationID(t *testing.T) {
	for _, id := range []string{" ", "\t", ".", "..", "\xff"} {
		t.Run(id, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Limits(t.Context(), &GetLimitsOptions{OrganizationID: id})
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestAccountLimitsRequiresOrganizationForJWT(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server, WithJWTToken("jwt"), WithProjectID("project-id"))
	// When
	_, err := client.Limits(t.Context(), nil)
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	require.Empty(t, fake.requests())
}

func TestAccountLimitsCachesOrganizationAcrossConcurrentCalls(t *testing.T) {
	// Given
	lookupStarted, releaseLookup := make(chan struct{}), make(chan struct{})
	var lookups atomic.Int64
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /api-keys/current": func(w http.ResponseWriter, r *http.Request) {
			if lookups.Add(1) == 1 {
				close(lookupStarted)
			}
			select {
			case <-releaseLookup:
				writeJSON(t, w, accountKeyFixture())
			case <-r.Context().Done():
			}
		},
		"GET /orgs/org-id/limits": jsonHandler(t, accountLimitsFixture()),
	})
	client := newTestClient(t, fake.Server)
	const callers = 8
	results := make(chan error, callers)
	var workers sync.WaitGroup
	// When
	for range callers {
		workers.Go(func() {
			_, err := client.Limits(t.Context(), nil)
			results <- err
		})
	}
	<-lookupStarted
	close(releaseLookup)
	workers.Wait()
	close(results)
	// Then
	for err := range results {
		require.NoError(t, err)
	}
	require.EqualValues(t, 1, lookups.Load())
	require.Len(t, fake.requests(), callers+1)
}

func TestAccountLimitsRetriesFailedOrganizationLookup(t *testing.T) {
	// Given
	var lookups atomic.Int64
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /api-keys/current": func(w http.ResponseWriter, _ *http.Request) {
			if lookups.Add(1) == 1 {
				w.WriteHeader(http.StatusForbidden)
				return
			}
			writeJSON(t, w, accountKeyFixture())
		},
		"GET /orgs/org-id/limits": jsonHandler(t, accountLimitsFixture()),
	})
	client := newTestClient(t, fake.Server)
	_, firstErr := client.Limits(t.Context(), nil)
	require.ErrorIs(t, firstErr, ErrForbidden)
	// When
	result, err := client.Limits(t.Context(), &GetLimitsOptions{})
	// Then
	require.NoError(t, err)
	require.EqualValues(t, 7, result.Sandboxes.Total.Usage)
	require.EqualValues(t, 2, lookups.Load())
	require.Len(t, fake.requests(), 3)
}

func TestAccountLimitsRejectsMalformedInferredOrganizationBeforeFollowup(t *testing.T) {
	for _, id := range []string{"", " ", ".", ".."} {
		t.Run(id, func(t *testing.T) {
			// Given
			fixture := accountKeyFixture()
			keyContext, ok := fixture["context"].(map[string]any)
			require.True(t, ok)
			keyContext["org_id"] = id
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /api-keys/current": jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Limits(t.Context(), nil)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestAccountLimitsWaiterCanCancelSharedLookup(t *testing.T) {
	// Given
	lookupStarted, releaseLookup := make(chan struct{}), make(chan struct{})
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /api-keys/current": func(w http.ResponseWriter, r *http.Request) {
			close(lookupStarted)
			select {
			case <-releaseLookup:
				writeJSON(t, w, accountKeyFixture())
			case <-r.Context().Done():
			}
		},
		"GET /orgs/org-id/limits": jsonHandler(t, accountLimitsFixture()),
	})
	client := newTestClient(t, fake.Server)
	first := make(chan error, 1)
	go func() {
		_, err := client.Limits(t.Context(), nil)
		first <- err
	}()
	<-lookupStarted
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	waiting := &accountObservedContext{Context: ctx, selected: make(chan struct{})}
	second := make(chan error, 1)
	// When
	go func() {
		_, err := client.Limits(waiting, nil)
		second <- err
	}()
	<-waiting.selected
	cancel()
	// Then
	require.ErrorIs(t, <-second, context.Canceled)
	close(releaseLookup)
	require.NoError(t, <-first)
	require.Len(t, fake.requests(), 2)
}

// accountObservedContext signals that a waiter reached the cancellable select.
type accountObservedContext struct {
	context.Context
	once     sync.Once
	selected chan struct{}
}

func (c *accountObservedContext) Done() <-chan struct{} {
	c.once.Do(func() { close(c.selected) })
	return c.Context.Done()
}
