// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func diskVolumeFixture(id string) string {
	return `{"created_at":"2026-09-13T00:00:00Z","updated_at":"2026-09-13T00:00:00Z","id":"` + id + `","name":"data","pending_final_commits":[],"size_gib":100,"status":"ready","type":"disk"}`
}

func bucketVolumeFixture(id string) string {
	return `{"created_at":"2026-09-13T00:00:00Z","updated_at":"2026-09-13T00:00:00Z","id":"` + id + `","name":"data","options":{"bucket":{"name":"corp-data","origin":"external","endpoint":"https://s3.example.com","region":"region-a","prefix":"team-a/","credentials":{"access_key_secret":"BUCKET_KEY","secret_key_secret":"BUCKET_SECRET"}}},"pending_final_commits":[],"size_gib":null,"status":"ready","type":"bucket"}`
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
			name:     "create volume of the server's type",
			args:     []string{"volume", "create", "data"},
			expected: []expectedRequest{{method: http.MethodPost, path: "/volumes", status: 201, bodyJSON: `{"name":"data"}`, response: bucketVolumeFixture("55555555-5555-5555-5555-555555555555")}},
		},
		{
			name:     "create volume of an explicit type",
			args:     []string{"volume", "create", "data", "--type", "disk", "--size-gib", "200"},
			expected: []expectedRequest{{method: http.MethodPost, path: "/volumes", status: 201, bodyJSON: `{"name":"data","type":"disk","size_gib":200}`, response: diskVolumeFixture("55555555-5555-5555-5555-555555555555")}},
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
			name:     "delete volume",
			args:     []string{"volume", "delete", "55555555-5555-5555-5555-555555555555", "--yes"},
			expected: []expectedRequest{{method: http.MethodDelete, path: "/volumes/55555555-5555-5555-5555-555555555555", status: 202, response: `{"id":"55555555-5555-5555-5555-555555555555","status":"deleting"}`}},
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

func TestVolumeGet_tableListsPendingFinalCommitsOnlyWhilePending(t *testing.T) {
	id := "55555555-5555-5555-5555-555555555555"
	path := "/volumes/" + id
	// Given a volume with two ended sandboxes whose last writes are not saved yet.
	pending := strings.Replace(diskVolumeFixture(id), `"pending_final_commits":[]`, `"pending_final_commits":[`+
		`{"sandbox_id":"sbx_1","status":"saving","ending_at":"2026-09-13T00:00:00Z","last_failure_at":null},`+
		`{"sandbox_id":"sbx_2","status":"future_status","ending_at":"2026-09-13T00:00:00Z","last_failure_at":"2026-09-13T00:05:00Z"}]`, 1)
	stdout, _, err := runCLIWithOutput(t, "table", []string{"volume", "get", id}, []expectedRequest{
		{method: http.MethodGet, path: path, status: 200, response: pending},
	})
	require.NoError(t, err)
	// Then each is named with its status, an unknown status as the API sent it.
	require.Contains(t, stdout, "pending_final_commits")
	require.Contains(t, stdout, "sbx_1 (saving), sbx_2 (future_status)")

	// A volume with nothing pending has no such row.
	stdout, _, err = runCLIWithOutput(t, "table", []string{"volume", "get", id}, []expectedRequest{
		{method: http.MethodGet, path: path, status: 200, response: diskVolumeFixture(id)},
	})
	require.NoError(t, err)
	require.NotContains(t, stdout, "pending_final_commits")
}

// A volume delete the API answers without an operation acknowledges the
// reference as given, after exactly one request.
func TestVolumeDelete_withoutAnOperationAcknowledgesTheReference(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"volume", "delete", "data", "--yes"}, []expectedRequest{
		{method: http.MethodDelete, path: "/volumes/data", status: 204},
	})
	require.NoError(t, err)
	require.True(t, isAck(t, stdout, "ref", "data", "deleted"), stdout)
}
