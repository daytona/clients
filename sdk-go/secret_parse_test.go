// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"fmt"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSecretMalformedTimestampsHaveStableOrder(t *testing.T) {
	// Given both timestamps malformed.
	fixture := secretFixture()
	fixture["created_at"], fixture["updated_at"] = "bad", "bad"
	data, err := json.Marshal(fixture)
	require.NoError(t, err)
	// When parsing repeatedly.
	for range 100 {
		_, parseErr := parseSecret(data)
		// Then creation is always checked before the update timestamp.
		require.ErrorContains(t, parseErr, "created_at")
	}
}

func TestSecretsRejectResponse_whenReadFieldIsMalformed(t *testing.T) {
	for _, tt := range []struct {
		field string
		value any
	}{
		{"name", nil},
		{"name", 3},
		{"allowed_domains", nil},
		{"allowed_domains", "host"},
		{"allowed_domains", []any{nil}},
		{"allowed_domains", []any{3}},
		{"description", true},
		{"created_at", nil},
		{"updated_at", nil},
		{"created_at", "today"},
		{"updated_at", "2026-01-01"},
	} {
		t.Run(tt.field+"_"+fmt.Sprint(tt.value), func(t *testing.T) {
			// Given
			fixture := secretFixture()
			fixture[tt.field] = tt.value
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /secrets/TOKEN": jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Secrets().Get(t.Context(), "TOKEN")
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, tt.field)
		})
	}
}

func TestSecretsList_whenPageIsMalformed(t *testing.T) {
	for name, fixture := range map[string]any{
		"array": []any{}, "null": nil, "missing": map[string]any{},
		"null array":  map[string]any{"secrets": nil},
		"scalar item": map[string]any{"secrets": []any{1}},
		"null item":   map[string]any{"secrets": []any{nil}},
		"cursor":      map[string]any{"secrets": []any{}, "next_cursor": 5},
	} {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /secrets": jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Secrets().List(t.Context(), nil)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}
