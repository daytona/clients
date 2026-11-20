// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestR4ParserSandbox_whenSeveralFieldsMalformed_reportsStableField(t *testing.T) {
	for _, test := range []struct {
		name   string
		fields []string
		first  string
	}{
		{"resource maxima", []string{"memory_max_mib", "disk_max_mib"}, "memory_max_mib"},
		{"timestamps", []string{
			"created_at", "updated_at", "started_at", "stopped_at",
			"last_activity_at", "create_timeout_at", "spot_evicted_at",
		}, "created_at"},
		{"optional booleans", []string{"spot", "persistent"}, "spot"},
		{"labels", []string{"labels"}, "labels.a"},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			body := sandboxFixture("sbx")
			for _, name := range test.fields {
				body[name] = "invalid"
			}
			if test.name == "labels" {
				body["labels"] = map[string]int{"z": 1, "a": 2}
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx": jsonHandler(t, body),
			})
			client := newTestClient(t, fake.Server)
			for range 20 {
				// When
				_, err := client.Get(t.Context(), "sbx", nil)
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				var sdkErr *Error
				require.ErrorAs(t, err, &sdkErr)
				require.Contains(t, sdkErr.Message, test.first)
			}
		})
	}
}
