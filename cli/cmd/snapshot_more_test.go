// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestSnapshotCreate_linuxRoute(t *testing.T) {
	stdout, _, err := runCLI(t, []string{
		"snapshot", "create", "22222222-2222-2222-2222-222222222222",
		"--name", "saved-linux", "--no-wait",
	}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "linux", "running")},
		{method: http.MethodPost, path: "/sandboxes/22222222-2222-2222-2222-222222222222/snapshots", status: 202, bodyJSON: `{"name":"saved-linux"}`, response: `{"id":"33333333-3333-3333-3333-333333333333","status":"pending"}`},
	})
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
}

func TestSnapshotCreate_macosRoute(t *testing.T) {
	stdout, _, err := runCLI(t, []string{
		"snapshot", "create", "22222222-2222-2222-2222-222222222222",
		"--name", "saved-macos", "--no-wait",
	}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "macos", "running")},
		{method: http.MethodPost, path: "/sandboxes/22222222-2222-2222-2222-222222222222/snapshots", status: 202, bodyJSON: `{"name":"saved-macos"}`, response: `{"id":"33333333-3333-3333-3333-333333333333","status":"pending"}`},
	})
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
}

func TestSnapshotDelete_requiresYes(t *testing.T) {
	_, _, err := runCLI(t, []string{"snapshot", "delete", "33333333-3333-3333-3333-333333333333"}, nil)
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}
