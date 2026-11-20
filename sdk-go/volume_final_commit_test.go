// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"fmt"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func volumeFinalCommitFixture() map[string]any {
	return map[string]any{
		"sandbox_id": "sbx_1", "status": "pending",
		"ending_at": "2026-01-01T00:00:00Z", "last_failure_at": nil,
	}
}

func TestVolumePendingFinalCommits_whenReported(t *testing.T) {
	for _, tt := range []struct {
		raw   string
		state VolumeFinalCommitState
	}{
		{"pending", VolumeFinalCommitStatePending},
		{"saving", VolumeFinalCommitStateSaving},
		{"failing", VolumeFinalCommitStateFailing},
		{"future", VolumeFinalCommitStateUnknown},
	} {
		t.Run(tt.raw, func(t *testing.T) {
			// Given
			fixture, commit := volumeFixture(), volumeFinalCommitFixture()
			commit["status"] = tt.raw
			commit["last_failure_at"] = "2026-01-02T00:00:00Z"
			fixture["pending_final_commits"] = []any{commit}
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /volumes/v": jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When
			got, err := client.Volumes().Get(t.Context(), "v", nil)
			// Then
			require.NoError(t, err)
			require.Len(t, got.PendingFinalCommits, 1)
			saved := got.PendingFinalCommits[0]
			require.Equal(t, "sbx_1", saved.SandboxID)
			require.Equal(t, tt.state, saved.State)
			require.Equal(t, tt.raw, saved.RawState)
			require.Equal(t, "2026-01-01", saved.EndingAt.Format("2006-01-02"))
			require.Equal(t, "2026-01-02", saved.LastFailureAt.Format("2006-01-02"))
		})
	}
}

func TestVolumePendingFinalCommits_whenOmitted(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /volumes/v": jsonHandler(t, volumeFixture())})
	client := newTestClient(t, fake.Server)
	// When
	got, err := client.Volumes().Get(t.Context(), "v", nil)
	// Then
	require.NoError(t, err)
	require.Equal(t, []VolumePendingFinalCommit{}, got.PendingFinalCommits)
}

func TestVolumePendingFinalCommits_whenMalformed(t *testing.T) {
	for _, malformed := range []any{nil, "pending", []any{nil}, []any{5}, []any{map[string]any{}}} {
		t.Run(fmt.Sprint(malformed), func(t *testing.T) {
			// Given
			fixture := volumeFixture()
			fixture["pending_final_commits"] = malformed
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /volumes/v": jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Volumes().Get(t.Context(), "v", nil)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}

func TestVolumeFinalCommit_whenFieldMalformed(t *testing.T) {
	for _, tt := range []struct {
		field string
		value any
	}{
		{"sandbox_id", nil},
		{"sandbox_id", 1},
		{"status", nil},
		{"status", false},
		{"ending_at", nil},
		{"ending_at", "today"},
		{"last_failure_at", false},
		{"last_failure_at", "tomorrow"},
	} {
		t.Run(tt.field+"_"+fmt.Sprint(tt.value), func(t *testing.T) {
			// Given
			fixture, commit := volumeFixture(), volumeFinalCommitFixture()
			commit[tt.field] = tt.value
			fixture["pending_final_commits"] = []any{commit}
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

func TestVolumeFinalCommit_whenNullableFieldIsMissing(t *testing.T) {
	// Given
	fixture, commit := volumeFixture(), volumeFinalCommitFixture()
	delete(commit, "last_failure_at")
	fixture["pending_final_commits"] = []any{commit}
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /volumes/v": jsonHandler(t, fixture)})
	client := newTestClient(t, fake.Server)
	// When
	_, err := client.Volumes().Get(t.Context(), "v", nil)
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	require.ErrorContains(t, err, "last_failure_at")
}

func TestVolumeErrors_whenServerReturnsKnownCode(t *testing.T) {
	for _, tt := range []struct {
		code     string
		status   int
		sentinel error
		category error
	}{
		{CodeVolumeNotFound, 404, ErrVolumeNotFound, ErrNotFound},
		{CodeVolumeFinalCommitPending, 409, ErrVolumeFinalCommitPending, ErrConflict},
		{CodeVolumesUnavailable, 503, ErrVolumesUnavailable, ErrServiceUnavailable},
	} {
		t.Run(tt.code, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /volumes/v": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(tt.status)
					writeJSON(t, w, map[string]string{"code": tt.code, "source": SourceAPI, "message": "unavailable"})
				},
			})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Volumes().Get(t.Context(), "v", nil)
			// Then
			require.ErrorIs(t, err, tt.sentinel)
			require.ErrorIs(t, err, tt.category)
			// Known API codes choose the category, even when an intermediary
			// reports a different status. Other sources retain HTTP fallback.
			require.ErrorIs(t, &Error{Code: tt.code, Source: SourceAPI, StatusCode: 400}, tt.category)
			require.ErrorIs(t, &Error{Code: tt.code, Source: SourceDaemon, StatusCode: 400}, ErrBadRequest)
		})
	}
}
