// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"net/url"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func snapshotFixture(id, status string) map[string]any {
	return map[string]any{
		"id": id, "name": "saved", "status": status, "class": "linux", "kind": "full",
		"region_id": "region-a", "region_ids": []string{"region-a", "region-b"},
		"created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
	}
}

func TestSnapshotsGet_whenFutureMetadataArrives(t *testing.T) {
	// Given unknown variants with distinct snapshot sizes and source identity.
	wire := snapshotFixture("snap", "future-status")
	wire["class"], wire["kind"], wire["future_field"] = "future-class", "future-kind", true
	wire["bytes"], wire["memory_bytes"], wire["sandbox_id"] = 2048, 1024, "sbx"
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /snapshots/snap": jsonHandler(t, wire)})
	client := newTestClient(t, fake.Server)
	// When reading the snapshot.
	snapshot, err := client.Snapshots().Get(t.Context(), "snap", nil)
	// Then normalized variants preserve their raw values and sizes stay distinct.
	require.NoError(t, err)
	require.Equal(t, SnapshotClassUnknown, snapshot.Class)
	require.Equal(t, "future-class", snapshot.RawClass)
	require.Equal(t, SnapshotKindUnknown, snapshot.Kind)
	require.Equal(t, "future-kind", snapshot.RawKind)
	require.Equal(t, SnapshotStatusUnknown, snapshot.Status)
	require.Equal(t, "future-status", snapshot.RawStatus)
	require.NotNil(t, snapshot.Bytes)
	require.NotNil(t, snapshot.MemoryBytes)
	require.Equal(t, int64(2048), *snapshot.Bytes)
	require.Equal(t, int64(1024), *snapshot.MemoryBytes)
	require.Equal(t, "sbx", snapshot.SandboxID)
	require.Equal(t, "saved", snapshot.Name)
	require.Equal(t, "region-a", snapshot.Region)
	require.Equal(t, []string{"region-a", "region-b"}, snapshot.AvailableIn)
	require.Equal(t, time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), snapshot.CreatedAt)
}

func TestSnapshotsGet_whenOptionalFieldsAreNull(t *testing.T) {
	// Given explicit nulls in optional metadata.
	wire := snapshotFixture("snap", "ready")
	for _, field := range []string{"bytes", "memory_bytes", "sandbox_id", "error"} {
		wire[field] = nil
	}
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /snapshots/snap": jsonHandler(t, wire)})
	client := newTestClient(t, fake.Server)
	// When reading.
	snapshot, err := client.Snapshots().Get(t.Context(), "snap", nil)
	// Then absence stays distinct from a reported size of zero.
	require.NoError(t, err)
	require.Nil(t, snapshot.Bytes)
	require.Nil(t, snapshot.MemoryBytes)
	require.Empty(t, snapshot.SandboxID)
	require.Empty(t, snapshot.Error)
	require.Equal(t, SnapshotKindFull, snapshot.Kind)
	require.Equal(t, SnapshotClassLinux, snapshot.Class)
}

func TestSnapshotsGet_whenRequiredFieldIsMissing(t *testing.T) {
	for field := range snapshotFixture("snap", "ready") {
		t.Run(field, func(t *testing.T) {
			// Given one absent required field.
			wire := snapshotFixture("snap", "ready")
			delete(wire, field)
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /snapshots/snap": jsonHandler(t, wire)})
			client := newTestClient(t, fake.Server)
			// When reading.
			_, err := client.Snapshots().Get(t.Context(), "snap", nil)
			// Then the SDK identifies the malformed field.
			require.ErrorIs(t, err, ErrBadRequest)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, SourceSDK, detail.Source)
			require.Contains(t, detail.Message, field)
		})
	}
}

func TestSnapshotsGet_whenFieldTypesAreMalformed(t *testing.T) {
	for _, tt := range []struct {
		field string
		value any
	}{
		{"id", true},
		{"name", 1},
		{"class", 1},
		{"kind", []any{}},
		{"status", nil},
		{"region_id", 1},
		{"region_ids", nil},
		{"region_ids", []any{nil}},
		{"region_ids", []any{false}},
		{"created_at", false},
		{"updated_at", "invalid"},
		{"bytes", "1"},
		{"memory_bytes", 1.25},
		{"sandbox_id", false},
		{"error", map[string]any{}},
	} {
		t.Run(tt.field, func(t *testing.T) {
			// Given a complete snapshot with a single malformed field.
			wire := snapshotFixture("snap", "ready")
			wire[tt.field] = tt.value
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /snapshots/snap": jsonHandler(t, wire)})
			client := newTestClient(t, fake.Server)
			// When mapping it.
			_, err := client.Snapshots().Get(t.Context(), "snap", nil)
			// Then no invalid snapshot is returned.
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}

func TestSnapshotsList_whenFiltersAreSet(t *testing.T) {
	// Given one snapshot page with a continuation.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /snapshots/search": jsonHandler(t, map[string]any{
			"snapshots": []any{snapshotFixture("snap", "ready")}, "next_cursor": "next",
		}),
	})
	client := newTestClient(t, fake.Server)
	// When requesting a filtered page.
	page, err := client.Snapshots().List(t.Context(), &ListSnapshotsOptions{
		Cursor: "first", Limit: 2, Name: "saved", SandboxID: "sbx",
	})
	// Then every filter reaches the server without fetching the continuation.
	require.NoError(t, err)
	require.Len(t, page.Items, 1)
	require.Equal(t, "snap", page.Items[0].ID)
	require.Equal(t, "next", page.NextCursor)
	requests := fake.requests()
	require.Len(t, requests, 1)
	require.Equal(t, url.Values{
		"cursor": {"first"}, "limit": {"2"}, "name": {"saved"}, "sandbox_id": {"sbx"},
	}, requests[0].Query)
}

func TestSnapshotsList_whenPageIsMalformed(t *testing.T) {
	for _, body := range []any{
		nil,
		[]any{},
		map[string]any{},
		map[string]any{"snapshots": nil},
		map[string]any{"snapshots": []any{nil}},
		map[string]any{"snapshots": []any{true}},
		map[string]any{"snapshots": []any{}, "next_cursor": false},
	} {
		t.Run("malformed page", func(t *testing.T) {
			// Given a malformed envelope or element.
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /snapshots/search": jsonHandler(t, body)})
			client := newTestClient(t, fake.Server)
			// When listing snapshots.
			_, err := client.Snapshots().List(t.Context(), nil)
			// Then the bad response is classified consistently.
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}
