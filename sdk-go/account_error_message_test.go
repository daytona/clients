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

func TestAccountMalformedObject_whenNestedPathIsReportedOnce(t *testing.T) {
	for _, tt := range []struct {
		read, field, path string
		value             any
	}{
		{"key", "context", "API key.context", false},
		{"limits", "sandboxes", "organization limits.sandboxes", false},
		{"limits", "storage", "organization limits.storage.images", map[string]any{"volumes": accountUsageFixture(), "images": false}},
		{"audit", "targets", "audit actions.targets[0]", []any{false}},
		{"audit", "targets", "audit actions.targets[0]", []any{nil}},
	} {
		t.Run(tt.path, func(t *testing.T) {
			// Given a malformed nested object or array item.
			read := accountReadByName(t, tt.read)
			fixture := read.fixture()
			fixture[tt.field] = tt.value
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET " + read.path: jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When parsing the account response.
			err := read.call(t.Context(), client)
			// Then the error reports the complete location once.
			require.ErrorIs(t, err, ErrBadRequest)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, SourceSDK, detail.Source)
			require.Equal(t, 1, strings.Count(detail.Message, tt.path), detail.Message)
			parts := strings.Split(tt.path, ".")
			require.Equal(t, 1, strings.Count(detail.Message, parts[len(parts)-1]), detail.Message)
		})
	}
}
