// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"fmt"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestVolumesRejectResponse_whenReadFieldIsMalformed(t *testing.T) {
	for _, tt := range []struct {
		field string
		value any
	}{
		{"id", nil},
		{"id", 3},
		{"name", nil},
		{"name", false},
		{"type", nil},
		{"type", 1},
		{"status", nil},
		{"status", true},
		{"created_at", nil},
		{"updated_at", nil},
		{"created_at", "not a date"},
		{"updated_at", 1},
		{"last_used_at", "2026-02-30T00:00:00Z"},
		{"used_bytes_at", "today"},
		{"error", false},
		{"size_gib", 1.5},
		{"used_bytes", "1"},
		{"used_bytes", 1.5},
		{"options", []string{}},
	} {
		t.Run(tt.field+"_"+fmt.Sprint(tt.value), func(t *testing.T) {
			// Given
			fixture := volumeFixture()
			fixture[tt.field] = tt.value
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /volumes/v": jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Volumes().Get(t.Context(), "v", nil)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, tt.field)
		})
	}
}

func TestVolumesCreate_whenServerReturnsBlankID(t *testing.T) {
	// Given
	fixture := volumeFixture()
	fixture["id"] = " "
	fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /volumes": jsonHandler(t, fixture)})
	client := newTestClient(t, fake.Server)
	// When
	_, err := client.Volumes().Create(t.Context(), "v", nil)
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	require.ErrorContains(t, err, "id")
}

func TestVolumesList_whenPageIsMalformed(t *testing.T) {
	for name, fixture := range map[string]any{
		"array": []any{}, "null": nil, "missing": map[string]any{},
		"null array":  map[string]any{"volumes": nil},
		"scalar item": map[string]any{"volumes": []any{1}},
		"null item":   map[string]any{"volumes": []any{nil}},
		"cursor":      map[string]any{"volumes": []any{}, "next_cursor": 5},
	} {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /volumes": jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Volumes().List(t.Context(), nil)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}

func TestVolumeEnums_whenServerReturnsKnownStatuses(t *testing.T) {
	for _, status := range []VolumeStatus{VolumeStatusReady, VolumeStatusDeleting, VolumeStatusDeleted, VolumeStatusError} {
		t.Run(string(status), func(t *testing.T) {
			// Given
			fixture := volumeFixture()
			fixture["status"], fixture["type"] = string(status), "bucket"
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /volumes/v": jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When
			got, err := client.Volumes().Get(t.Context(), "v", nil)
			// Then
			require.NoError(t, err)
			require.Equal(t, status, got.Status)
			require.Equal(t, string(status), got.RawStatus)
			require.Equal(t, VolumeTypeBucket, got.Type)
		})
	}
}
