// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestVolumesGet_whenMultipleFieldsAreMalformedReportsFirstConsistently(t *testing.T) {
	// Given two malformed optional integer fields.
	fixture := volumeFixture()
	fixture["size_gib"], fixture["used_bytes"] = "bad size", "bad usage"
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /volumes/v": jsonHandler(t, fixture)})
	client := newTestClient(t, fake.Server)
	for range 256 {
		// When reading the same malformed response repeatedly.
		_, err := client.Volumes().Get(t.Context(), "v", nil)
		// Then capacity is always reported before usage.
		require.ErrorIs(t, err, ErrBadRequest)
		require.ErrorContains(t, err, "size_gib")
	}
}
