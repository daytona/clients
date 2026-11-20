// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"math"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestSearch_whenAllFiltersAreSet(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/search": jsonHandler(t, map[string]any{
		"sandboxes": []any{sandboxFixture("sbx")}, "total": 7, "total_relation": "future", "next_cursor": "next",
	})})
	client := newTestClient(t, fake.Server)
	minCores, maxCores, spot, minMemory, maxMemory, minDisk, maxDisk := 0.5, 2.0, false, int64(0), int64(2048), int64(1), int64(4096)
	stamp := time.Date(2026, 1, 1, 0, 0, 0, 0, time.FixedZone("offset", 3600))
	// When
	page, err := client.Search(t.Context(), &SearchOptions{
		Q: "query", NamePrefix: "name", IDPrefix: "id", States: []SandboxState{SandboxStateCreating, SandboxStateError},
		Classes: []SandboxClass{SandboxClassLinux}, Regions: []string{"a", "b"}, ImageRef: "image", ImageRefPrefix: "prefix",
		Labels: map[string]string{"team": "sdk"}, Spot: &spot, ForkedFrom: "parent", MinCores: &minCores, MaxCores: &maxCores,
		MinMemoryMiB: &minMemory, MaxMemoryMiB: &maxMemory, MinDiskMiB: &minDisk, MaxDiskMiB: &maxDisk,
		CreatedStartAt: stamp, CreatedEndAt: stamp, UpdatedStartAt: stamp, UpdatedEndAt: stamp,
		LastActivityStartAt: stamp, LastActivityEndAt: stamp, Sort: SearchSortName, Order: SortOrderAscending,
		Cursor: "opaque", Limit: 10,
	})
	// Then
	require.NoError(t, err)
	require.EqualValues(t, 7, page.Total)
	require.Equal(t, TotalRelationUnknown, page.TotalRelation)
	require.Equal(t, "future", page.RawTotalRelation)
	require.Equal(t, "next", page.NextCursor)
	query := fake.requests()[0].Query
	require.Equal(t, "pending,assigned,failed,lost", query.Get("status"))
	require.Equal(t, "a,b", query.Get("region_ids"))
	require.Equal(t, "false", query.Get("spot"))
	require.Equal(t, "0", query.Get("min_memory_mib"))
	require.Equal(t, "2025-12-31T23:00:00Z", query.Get("created_start_at"))
	require.JSONEq(t, `{"team":"sdk"}`, query.Get("labels"))
	require.Len(t, query, 27)
}

func TestSearch_whenInvalidOptionsArePassed(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	nan, unsafe := math.NaN(), int64(maxSafeInteger+1)
	for _, options := range []SearchOptions{
		{States: []SandboxState{}},
		{States: []SandboxState{SandboxStateUnknown}},
		{Classes: []SandboxClass{}},
		{Classes: []SandboxClass{SandboxClassUnknown}},
		{Regions: []string{}},
		{Regions: []string{"a,b"}},
		{Labels: map[string]string{}},
		{Labels: map[string]string{"": "value"}},
		{MinCores: &nan},
		{MinMemoryMiB: &unsafe},
		{Sort: "future"},
		{Order: "future"},
		{Limit: -1},
		{Limit: 201},
		{Q: "\xff"},
		{CreatedStartAt: time.Date(10000, 1, 1, 0, 0, 0, 0, time.UTC)},
	} {
		// When
		_, err := client.Search(t.Context(), &options)
		// Then
		require.ErrorIs(t, err, ErrBadRequest)
	}
	require.Empty(t, fake.requests())
}

func TestList_whenSearchUnavailableIsRememberedAndReprobed(t *testing.T) {
	// Given
	var searches, lists atomic.Int32
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/search": func(w http.ResponseWriter, _ *http.Request) {
			if searches.Add(1) == 1 {
				w.WriteHeader(404)
				return
			}
			writeJSON(t, w, map[string]any{"sandboxes": []any{sandboxFixture("search")}})
		},
		"GET /sandboxes": func(w http.ResponseWriter, _ *http.Request) {
			if lists.Add(1) == 2 {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(410)
				writeJSON(t, w, map[string]string{"code": "GONE"})
				return
			}
			writeJSON(t, w, map[string]any{"sandboxes": []any{sandboxFixture("plain")}})
		},
	})
	client := newTestClient(t, fake.Server)
	initial, err := client.List(t.Context(), nil)
	require.NoError(t, err)
	require.Equal(t, "plain", initial.Items[0].ID())
	// When
	page, err := client.List(t.Context(), nil)
	// Then
	require.NoError(t, err)
	require.Equal(t, "search", page.Items[0].ID())
	require.EqualValues(t, 2, searches.Load())
	require.EqualValues(t, 2, lists.Load())
}

func TestListAll_whenCursorCycles(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/search": func(w http.ResponseWriter, r *http.Request) {
		id := "first"
		if r.URL.Query().Get("cursor") != "" {
			id = "second"
		}
		writeJSON(t, w, map[string]any{"sandboxes": []any{sandboxFixture(id)}, "next_cursor": "repeated"})
	}})
	client := newTestClient(t, fake.Server)
	// When
	var ids []string
	for sandbox, err := range client.ListAll(t.Context(), nil) {
		require.NoError(t, err)
		ids = append(ids, sandbox.ID())
	}
	// Then
	require.Equal(t, []string{"first", "second"}, ids)
	require.Len(t, fake.requests(), 2)
}

func TestFindByName_whenMatchesSpanPages(t *testing.T) {
	// Given
	deleted := sandboxFixture("deleted")
	deleted["status"] = "deleted"
	delete(deleted, "toolbox_proxy_url") // Must be filtered before detail fallback.
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/search": func(w http.ResponseWriter, r *http.Request) {
		cursor, id := "next", "first"
		if r.URL.Query().Get("cursor") != "" {
			cursor, id = "", "second"
		}
		writeJSON(t, w, map[string]any{
			"sandboxes": []any{sandboxFixture(id), deleted}, "total": 4, "total_relation": "eq", "next_cursor": cursor,
		})
	}})
	client := newTestClient(t, fake.Server)
	// When
	items, err := client.FindByName(t.Context(), "my-name", &FindByNameOptions{Region: "region-a"})
	// Then
	require.NoError(t, err)
	require.Len(t, items, 2)
	require.Len(t, fake.requests(), 2)
	for _, request := range fake.requests() {
		require.Equal(t, "region-a", request.Header.Get("X-Daytona-Region"))
		require.JSONEq(t, `{"name":"my-name"}`, request.Query.Get("labels"))
		require.Equal(t, "100", request.Query.Get("limit"))
	}
}
