// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestImageGet_whenMultipleFieldsAreMalformedReportsFirstConsistently(t *testing.T) {
	for _, fields := range [][2]string{
		{"id", "class"},
		{"anonymous", "format_outdated"},
		{"created_at", "updated_at"},
		{"bytes", "layer_count"},
	} {
		t.Run(fields[0], func(t *testing.T) {
			// Given two malformed fields in the same parsing group.
			fixture := imageFixture("img", "ready")
			fixture[fields[0]], fixture[fields[1]] = []any{}, []any{}
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /images/img": jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			for range 256 {
				// When reading the same malformed response repeatedly.
				_, err := client.Images().Get(t.Context(), "img", nil)
				// Then the first field is reported regardless of map iteration order.
				require.ErrorIs(t, err, ErrBadRequest)
				require.ErrorContains(t, err, "response field "+fields[0]+" ")
			}
		})
	}
}
