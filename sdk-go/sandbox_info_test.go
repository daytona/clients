// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestGet_whenMetadataIncludesFutureValues(t *testing.T) {
	// Given
	fixture := sandboxFixture("sbx")
	fixture["class"], fixture["status"], fixture["mac_boot_mode"] = "future-os", "future-state", "future-mode"
	fixture["memory_max_mib"], fixture["disk_max_mib"] = 2048, 4096
	fixture["labels"] = map[string]string{"team": "sdk"}
	fixture["image"] = map[string]string{"id": "image-1", "name": "image"}
	fixture["persistent"], fixture["spot"] = false, true
	fixture["lifecycle_policy"] = map[string]any{
		"timeout_seconds": 30, "idle_expiry_seconds": 0, "on_timeout": "future-action",
		"timeout_at": "2026-01-01t12:00:00z",
	}
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx": jsonHandler(t, fixture)})
	client := newTestClient(t, fake.Server)
	// When
	sandbox, err := client.Get(t.Context(), "sbx", nil)
	// Then
	require.NoError(t, err)
	info := sandbox.Info()
	require.Equal(t, SandboxStateUnknown, info.State)
	require.Equal(t, "future-state", info.RawState)
	require.Equal(t, SandboxClassUnknown, info.Class)
	require.Equal(t, "future-os", info.RawClass)
	require.Equal(t, MacBootMode("future-mode"), info.MacBootMode)
	require.InDelta(t, 1, info.Resources.MemoryGiB, 0)
	require.InDelta(t, 2, *info.Resources.MemoryMaxGiB, 0)
	require.InDelta(t, 4, *info.Resources.DiskMaxGiB, 0)
	require.Equal(t, 30*time.Second, *info.LifecyclePolicy.Timeout)
	require.Zero(t, *info.LifecyclePolicy.IdleExpiry)
	require.Equal(t, LifecycleAction("future-action"), info.LifecyclePolicy.OnTimeout)
	info.Labels["team"] = "changed"
	info.Image.ID = "changed"
	*info.Resources.CPUMax = 99
	*info.LifecyclePolicy.Timeout = 99
	original := sandbox.Info()
	require.Equal(t, "sdk", original.Labels["team"])
	require.Equal(t, "image-1", original.Image.ID)
	require.InDelta(t, 2, *original.Resources.CPUMax, 0)
	require.Equal(t, 30*time.Second, *original.LifecyclePolicy.Timeout)
}

func TestGet_whenRequiredResponseFieldIsMissing(t *testing.T) {
	for _, field := range []string{"id", "class", "name", "status", "cpu", "cores_max", "memory_mib", "disk_mib", "port_map", "region_id"} {
		t.Run(field, func(t *testing.T) {
			// Given
			fixture := sandboxFixture("sbx")
			delete(fixture, field)
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx": jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Get(t.Context(), "sbx", nil)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Contains(t, err.Error(), field)
		})
	}
}

func TestGet_whenResponseFieldsAreMalformed(t *testing.T) {
	for _, test := range []struct {
		field string
		value any
	}{
		{"id", " "},
		{"class", "\t"},
		{"cpu", "1"},
		{"memory_mib", 1.5},
		{"port_map", nil},
		{"port_map", []any{map[string]any{"host_port": 2}}},
		{"labels", map[string]any{"x": nil}},
		{"labels", []string{}},
		{"image", map[string]any{"id": 4}},
		{"spot", "true"},
		{"created_at", "2026-02-30T00:00:00Z"},
		{"updated_at", "0000-01-01T00:00:00Z"},
		{"spot_evicted_at", "2026-01-01T00:00:00+24:00"},
		{"lifecycle_policy", map[string]any{"on_timeout": 8}},
	} {
		t.Run(test.field, func(t *testing.T) {
			// Given
			fixture := sandboxFixture("sbx")
			fixture[test.field] = test.value
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx": jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Get(t.Context(), "sbx", nil)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}

func TestGet_whenCallerInputIsInvalid(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	for _, id := range []string{"", " ", ".", "..", "\xff"} {
		// When/Then
		_, err := client.Get(t.Context(), id, nil)
		require.ErrorIs(t, err, ErrBadRequest)
	}
	for _, options := range []GetSandboxOptions{
		{AccessToken: " padded "},
		{AccessToken: "a\nb"},
		{AccessToken: "é"},
		{ToolboxURL: " "},
		{ToolboxURL: "https://{sandboxId}.invalid"},
	} {
		_, err := client.Get(t.Context(), "sbx", &options)
		require.ErrorIs(t, err, ErrBadRequest)
	}
	require.Empty(t, fake.requests())
}

func TestRefresh_whenReadersRunConcurrently(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx": jsonHandler(t, sandboxFixture("sbx"))})
	client := newTestClient(t, fake.Server)
	sandbox, err := client.Get(t.Context(), "sbx", nil)
	require.NoError(t, err)
	var workers sync.WaitGroup
	failures := make(chan error, 8)
	// When
	for range 8 {
		workers.Go(func() {
			for range 10 {
				sandbox.Info()
				sandbox.State()
				sandbox.Name()
			}
			failures <- sandbox.Refresh(t.Context())
		})
	}
	workers.Wait()
	close(failures)
	// Then
	for err := range failures {
		require.NoError(t, err)
	}
	require.Equal(t, "sbx", sandbox.ID())
	require.Equal(t, SandboxStateStarted, sandbox.State())
}
