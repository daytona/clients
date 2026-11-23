// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestReservationsListSinglePage(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /reservations": jsonHandler(t, map[string]any{"reservations": []any{reservationFixture()}, "next_cursor": "next"}),
	})
	service := newTestClient(t, fake.Server).Reservations()
	// When
	page, err := service.List(t.Context(), &ListReservationsOptions{Cursor: "a+/?", Limit: 200})
	// Then
	require.NoError(t, err)
	require.Len(t, page.Items, 1)
	require.Equal(t, "next", page.NextCursor)
	requests := fake.requests()
	require.Len(t, requests, 1)
	require.Equal(t, "a+/?", requests[0].Query.Get("cursor"))
	require.Equal(t, "200", requests[0].Query.Get("limit"))
	require.Empty(t, requests[0].Body)
}

func TestReservationsListAllSnapshotsOptionsAndStopsAtCycle(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /reservations": func(w http.ResponseWriter, r *http.Request) {
			next := map[string]string{"initial": "second", "second": "third", "third": "second"}[r.URL.Query().Get("cursor")]
			writeJSON(t, w, map[string]any{"reservations": []any{reservationFixture()}, "next_cursor": next})
		},
	})
	service := newTestClient(t, fake.Server).Reservations()
	opts := &ListReservationsOptions{Cursor: "initial", Limit: 3}
	iterator := service.ListAll(t.Context(), opts)
	opts.Cursor, opts.Limit = "mutated", 9
	require.Empty(t, fake.requests())
	// When
	count := 0
	for item, err := range iterator {
		require.NoError(t, err)
		require.Equal(t, "region-a:reservation-1", item.ID)
		count++
	}
	// Then
	require.Equal(t, 3, count)
	requests := fake.requests()
	require.Len(t, requests, 3)
	require.Equal(t, "initial", requests[0].Query.Get("cursor"))
	for _, request := range requests {
		require.Equal(t, "3", request.Query.Get("limit"))
	}
}

func TestReservationsListAllStopsOnBreak(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /reservations": jsonHandler(t, map[string]any{"reservations": []any{reservationFixture()}, "next_cursor": "next"}),
	})
	service := newTestClient(t, fake.Server).Reservations()
	// When
	for _, err := range service.ListAll(t.Context(), nil) {
		require.NoError(t, err)
		break
	}
	// Then
	require.Len(t, fake.requests(), 1)
}

func TestReservationsListAllYieldsErrorOnce(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	service := newTestClient(t, fake.Server).Reservations()
	// When
	count := 0
	for _, err := range service.ListAll(t.Context(), nil) {
		require.ErrorIs(t, err, ErrNotFound)
		count++
	}
	// Then
	require.Equal(t, 1, count)
	require.Len(t, fake.requests(), 1)
}

func TestReservationsListAllCancellation(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	service := newTestClient(t, fake.Server).Reservations()
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	// When
	count := 0
	for _, err := range service.ListAll(ctx, nil) {
		require.ErrorIs(t, err, context.Canceled)
		count++
	}
	// Then
	require.Equal(t, 1, count)
	require.Empty(t, fake.requests())
}

func TestReservationsListEmptyPage(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /reservations": jsonHandler(t, map[string]any{"reservations": []any{}, "next_cursor": nil}),
	})
	service := newTestClient(t, fake.Server).Reservations()
	// When
	page, err := service.List(t.Context(), nil)
	// Then
	require.NoError(t, err)
	require.Empty(t, page.Items)
	require.NotNil(t, page.Items)
	require.Empty(t, page.NextCursor)
	require.Empty(t, fake.requests()[0].Query)
}
