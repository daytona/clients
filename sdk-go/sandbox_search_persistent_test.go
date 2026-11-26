// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSearch_whenPersistenceFilterIsSupplied(t *testing.T) {
	persistent, ephemeral := true, false
	for _, tc := range []struct {
		name    string
		options *SearchSandboxesOptions
		want    []string
	}{
		{"true", &SearchSandboxesOptions{Persistent: &persistent}, []string{"true"}},
		{"false", &SearchSandboxesOptions{Persistent: &ephemeral}, []string{"false"}},
		{"nil field", &SearchSandboxesOptions{}, nil},
		{"nil options", nil, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/search": jsonHandler(t, map[string]any{
					"sandboxes": []any{}, "total": 0, "total_relation": "eq",
				}),
			})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Search(t.Context(), tc.options)
			// Then
			require.NoError(t, err)
			requests := fake.requests()
			require.Len(t, requests, 1)
			require.Equal(t, tc.want, requests[0].Query["persistent"])
		})
	}
}

func TestSearchAll_whenPersistenceChangesAfterIteratorCreation(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/search": jsonHandler(t, map[string]any{
			"sandboxes": []any{}, "total": 0, "total_relation": "eq",
		}),
	})
	client := newTestClient(t, fake.Server)
	persistent := false
	items := client.SearchAll(t.Context(), &SearchSandboxesOptions{Persistent: &persistent})
	persistent = true
	// When
	for _, err := range items {
		require.NoError(t, err)
	}
	// Then
	requests := fake.requests()
	require.Len(t, requests, 1)
	require.Equal(t, "false", requests[0].Query.Get("persistent"))
}
