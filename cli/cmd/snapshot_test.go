// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestSnapshotCommands_routeAndRenderJSON(t *testing.T) {
	tests := []struct {
		name     string
		args     []string
		expected []expectedRequest
		wantExit int
	}{
		{
			name: "create linux snapshot waits for ready",
			args: []string{"snapshot", "create", "11111111-1111-1111-1111-111111111111", "--name", "snap1"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
				{method: http.MethodPost, path: "/sandboxes/11111111-1111-1111-1111-111111111111/snapshots", status: 202, bodyJSON: `{"name":"snap1"}`, response: `{"id":"33333333-3333-3333-3333-333333333333","status":"pending"}`},
				{method: http.MethodGet, path: "/snapshots/33333333-3333-3333-3333-333333333333", query: "wait=ready&timeout_ms=55000", status: 200, response: snapshotFixture("33333333-3333-3333-3333-333333333333", "full", "ready")},
			},
		},
		{
			name: "create linux snapshot",
			args: []string{"snapshot", "create", "11111111-1111-1111-1111-111111111111", "--name", "snap1", "--no-wait"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
				{method: http.MethodPost, path: "/sandboxes/11111111-1111-1111-1111-111111111111/snapshots", status: 202, bodyJSON: `{"name":"snap1"}`, response: `{"id":"33333333-3333-3333-3333-333333333333","status":"pending"}`},
			},
		},
		{
			name: "delete snapshot",
			args: []string{"snapshot", "delete", "33333333-3333-3333-3333-333333333333", "--yes"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/snapshots/33333333-3333-3333-3333-333333333333", status: 200, response: snapshotFixture("33333333-3333-3333-3333-333333333333", "full", "ready")},
				{method: http.MethodDelete, path: "/snapshots/33333333-3333-3333-3333-333333333333", query: "id_only=true", status: 204, response: ``},
			},
		},
		{
			name:     "list snapshots",
			args:     []string{"snapshot", "list", "--limit", "1"},
			expected: []expectedRequest{{method: http.MethodGet, path: "/snapshots", query: "limit=1", status: 200, response: snapshotPageFixture("33333333-3333-3333-3333-333333333333")}},
		},
		{
			name:     "get snapshot",
			args:     []string{"snapshot", "get", "33333333-3333-3333-3333-333333333333"},
			expected: []expectedRequest{{method: http.MethodGet, path: "/snapshots/33333333-3333-3333-3333-333333333333", status: 200, response: snapshotFixture("33333333-3333-3333-3333-333333333333", "full", "ready")}},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			stdout, _, err := runCLI(t, tt.args, tt.expected)
			if tt.wantExit != 0 {
				require.Error(t, err)
				require.Equal(t, tt.wantExit, cerr.ExitCode(err))
				return
			}
			require.NoError(t, err)
			require.NotEmpty(t, requireJSONStdout(t, stdout))
			if tt.name == "delete snapshot" {
				require.True(t, isAck(t, stdout, "id", "33333333-3333-3333-3333-333333333333", "deleted"), stdout)
			}
		})
	}
}
