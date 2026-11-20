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

func TestSnapshotsList_whenFallbackIsIndependentAndRecovers(t *testing.T) {
	// Given a legacy snapshot deployment whose image search remains available.
	var searches, lists atomic.Int32
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /snapshots/search": func(w http.ResponseWriter, _ *http.Request) {
			if searches.Add(1) == 1 {
				w.WriteHeader(404)
				return
			}
			writeJSON(t, w, map[string]any{"snapshots": []any{snapshotFixture("search", "ready")}})
		},
		"GET /snapshots": func(w http.ResponseWriter, _ *http.Request) {
			if lists.Add(1) == 2 {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(410)
				writeJSON(t, w, map[string]string{"code": "GONE", "source": "API"})
				return
			}
			writeJSON(t, w, map[string]any{"snapshots": []any{snapshotFixture("legacy", "ready")}})
		},
		"GET /images/search": jsonHandler(t, map[string]any{"images": []any{imageFixture("img", "ready")}}),
	})
	client := newTestClient(t, fake.Server)
	initial, err := client.Snapshots().List(t.Context(), nil)
	require.NoError(t, err)
	require.Equal(t, "legacy", initial.Items[0].ID)
	// When making new service calls after fallback was learned.
	images, imageErr := client.Images().List(t.Context(), nil)
	recovered, snapshotErr := client.Snapshots().List(t.Context(), nil)
	// Then fallback affects only snapshots and their new handle re-probes on GONE.
	require.NoError(t, imageErr)
	require.NoError(t, snapshotErr)
	require.Equal(t, "img", images.Items[0].ID)
	require.Equal(t, "search", recovered.Items[0].ID)
	require.EqualValues(t, 2, searches.Load())
	require.EqualValues(t, 2, lists.Load())
}

func TestSnapshotsListAll_whenCursorCycles(t *testing.T) {
	// Given a continuation returning to the initial cursor.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /snapshots/search": func(w http.ResponseWriter, r *http.Request) {
			cursor := r.URL.Query().Get("cursor")
			next := "first"
			if cursor == "first" {
				next = "second"
			}
			writeJSON(t, w, map[string]any{"snapshots": []any{snapshotFixture(cursor, "ready")}, "next_cursor": next})
		},
	})
	client := newTestClient(t, fake.Server)
	// When traversing all snapshots.
	var ids []string
	for snapshot, err := range client.Snapshots().ListAll(t.Context(), &ListSnapshotsOptions{Cursor: "first"}) {
		require.NoError(t, err)
		ids = append(ids, snapshot.ID)
	}
	// Then every cursor is fetched at most once.
	require.Equal(t, []string{"first", "second"}, ids)
	require.Len(t, fake.requests(), 2)
}

func TestArtifactLists_whenOptionsAreInvalid(t *testing.T) {
	for _, options := range []ListImagesOptions{
		{Limit: -1}, {Limit: 201}, {Cursor: "\xff"}, {Name: "\xff"}, {SandboxID: "\xff"}, {Platform: "\xff"},
	} {
		t.Run("images", func(t *testing.T) {
			// Given invalid page or resource filters.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When listing images.
			_, err := client.Images().List(t.Context(), &options)
			// Then validation is local.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
	for _, options := range []ListSnapshotsOptions{
		{Limit: -1}, {Limit: 201}, {Cursor: "\xff"}, {Name: "\xff"}, {SandboxID: "\xff"},
	} {
		t.Run("snapshots", func(t *testing.T) {
			// Given invalid page or resource filters.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When listing snapshots.
			_, err := client.Snapshots().List(t.Context(), &options)
			// Then validation is local.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestImagesListAll_whenCallerCancelsBeforeIteration(t *testing.T) {
	// Given an iterator obtained before its context is canceled.
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	ctx, cancel := context.WithCancel(t.Context())
	images := client.Images().ListAll(ctx, nil)
	cancel()
	// When beginning iteration.
	var failures []error
	for image, err := range images {
		require.Nil(t, image)
		failures = append(failures, err)
	}
	// Then cancellation is yielded once without fetching a page.
	require.Len(t, failures, 1)
	require.ErrorIs(t, failures[0], context.Canceled)
	require.Empty(t, fake.requests())
}

func TestImagesListAll_whenNextPageFails(t *testing.T) {
	// Given a page followed by a server permission error.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /images/search": func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Query().Get("cursor") != "" {
				w.WriteHeader(403)
				return
			}
			writeJSON(t, w, map[string]any{"images": []any{imageFixture("img", "ready")}, "next_cursor": "next"})
		},
	})
	client := newTestClient(t, fake.Server)
	// When consuming the iterator.
	var ids []string
	var failures []error
	for image, err := range client.Images().ListAll(t.Context(), nil) {
		if err != nil {
			failures = append(failures, err)
		} else {
			ids = append(ids, image.ID)
		}
	}
	// Then the first page survives and the error ends traversal once.
	require.Equal(t, []string{"img"}, ids)
	require.Len(t, failures, 1)
	require.ErrorIs(t, failures[0], ErrForbidden)
	require.Len(t, fake.requests(), 2)
}
