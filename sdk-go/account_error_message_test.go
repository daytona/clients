// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestAccountMalformedResponseHasOneErrorEnvelope(t *testing.T) {
	for _, read := range accountTestReads() {
		t.Run(read.name, func(t *testing.T) {
			// Given an invalid value nested inside a valid response object.
			fixture := read.fixture()
			switch read.name {
			case "key":
				fixture["context"] = map[string]any{"org_id": false}
			case "budget":
				fixture["period_start_date"] = "2026-02-30"
			case "limits":
				fixture["sandboxes"] = map[string]any{"concurrent": map[string]any{"usage": false, "limit": 1}}
			case "audit":
				fixture["targets"] = []any{map[string]any{"target_type": false, "actions": []string{}}}
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET " + read.path: jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When parsing the response.
			err := read.call(t.Context(), client)
			// Then only Error.Error formats the SDK prefix and code/status suffix.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Equal(t, 1, strings.Count(err.Error(), "daytona:"))
			require.Equal(t, 1, strings.Count(err.Error(), "(BAD_REQUEST, HTTP 400)"))
		})
	}
}
