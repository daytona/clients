// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"os"
	"path/filepath"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestSandboxCP_stopsBeforeSigning_whenDirectoryProbeFails(t *testing.T) {
	for _, tc := range []struct {
		name   string
		status int
		body   string
		exit   int
	}{
		{"permission", 403, `{"code":"FILE_ACCESS_DENIED","message":"denied"}`, cerr.ExitForbidden},
		{"server", 500, `{"code":"INTERNAL_ERROR","message":"failed"}`, cerr.ExitFailure},
		{"other not found", 404, `{"code":"PROCESS_NOT_FOUND","message":"missing"}`, cerr.ExitNotFound},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given a local file and a failed destination metadata request.
			src := filepath.Join(t.TempDir(), "source")
			require.NoError(t, os.WriteFile(src, []byte("payload"), 0o600))
			// When uploading onto that destination.
			_, _, err := runToolboxCLI(t, []string{"sandbox", "cp", src, "sbx:/destination"}, []toolboxExpectedRequest{
				{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
				{method: http.MethodGet, path: "/test-tools/sbx/files/info", status: tc.status, auth: true, response: tc.body},
			})
			// Then the probe's failure is returned without signing or uploading.
			require.Equal(t, tc.exit, cerr.ExitCode(err))
		})
	}
}
