// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func diskVolumeFixture(id string) string {
	return `{"created_at":"2026-09-13T00:00:00Z","updated_at":"2026-09-13T00:00:00Z","id":"` + id + `","name":"data","size_gib":100,"status":"ready","type":"disk"}`
}

func bucketVolumeFixture(id string) string {
	return `{"created_at":"2026-09-13T00:00:00Z","updated_at":"2026-09-13T00:00:00Z","id":"` + id + `","name":"data","options":{"bucket":{"name":"corp-data","origin":"external","endpoint":"https://s3.example.com","region":"region-a","prefix":"team-a/","credentials":{"access_key_secret":"BUCKET_KEY","secret_key_secret":"BUCKET_SECRET"}}},"size_gib":null,"status":"ready","type":"bucket"}`
}

func TestVolumeCommands_routeAndRenderJSON(t *testing.T) {
	tests := []struct {
		name     string
		args     []string
		expected []expectedRequest
	}{
		{
			name:     "create volume",
			args:     []string{"volume", "create", "data", "--size-gib", "200"},
			expected: []expectedRequest{{method: http.MethodPost, path: "/volumes", status: 201, bodyJSON: `{"name":"data","size_gib":200}`, response: diskVolumeFixture("55555555-5555-5555-5555-555555555555")}},
		},
		{
			name:     "list volumes",
			args:     []string{"volume", "list"},
			expected: []expectedRequest{{method: http.MethodGet, path: "/volumes", query: "limit=100", status: 200, response: `{"volumes":[` + diskVolumeFixture("55555555-5555-5555-5555-555555555555") + `]}`}},
		},
		{
			name:     "get volume",
			args:     []string{"volume", "get", "55555555-5555-5555-5555-555555555555"},
			expected: []expectedRequest{{method: http.MethodGet, path: "/volumes/55555555-5555-5555-5555-555555555555", status: 200, response: diskVolumeFixture("55555555-5555-5555-5555-555555555555")}},
		},
		{
			name: "delete volume",
			args: []string{"volume", "delete", "55555555-5555-5555-5555-555555555555", "--yes"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/volumes/55555555-5555-5555-5555-555555555555", status: 200, response: diskVolumeFixture("55555555-5555-5555-5555-555555555555")},
				{method: http.MethodDelete, path: "/volumes/55555555-5555-5555-5555-555555555555", query: "id_only=true", status: 202, response: `{"id":"55555555-5555-5555-5555-555555555555","status":"deleting"}`},
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Given the expected API requests, when the command executes.
			stdout, _, err := runCLI(t, tt.args, tt.expected)
			// Then it returns the resource as JSON.
			require.NoError(t, err)
			require.NotEmpty(t, requireJSONStdout(t, stdout))
		})
	}
}

func TestVolumeList_tableNamesTheCapacityColumnInGiB(t *testing.T) {
	stdout, _, err := runCLIWithOutput(t, "table", []string{"volume", "list"}, []expectedRequest{
		{method: http.MethodGet, path: "/volumes", query: "limit=100", status: 200, response: `{"volumes":[` + diskVolumeFixture("55555555-5555-5555-5555-555555555555") + `]}`},
	})
	require.NoError(t, err)
	require.Contains(t, stdout, "SIZE_GIB")
	require.NotContains(t, stdout, "SIZE_GB")
}
