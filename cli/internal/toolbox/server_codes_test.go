// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

// The file, interpreter and git failures the sandbox reports exit with the
// documented status mapping and keep their code.
func TestMapError_sandboxCodesExitWithTheStatusMapping(t *testing.T) {
	cases := []struct {
		name   string
		status int
		code   string
		body   string
		exit   int
	}{
		{"file access denied", 403, "FILE_ACCESS_DENIED", `{"code":"FILE_ACCESS_DENIED","message":"/root/x: permission denied","source":"DAYTONA_DAEMON"}`, cerr.ExitForbidden},
		{"relative path", 400, "INVALID_FILE_PATH", `{"code":"INVALID_FILE_PATH","message":"path must be absolute","source":"DAYTONA_DAEMON"}`, cerr.ExitUsage},
		{"not a regular file", 400, "INVALID_FILE_PATH", `{"code":"INVALID_FILE_PATH","message":"/dev/null: not a regular file","source":"DAYTONA_DAEMON"}`, cerr.ExitUsage},
		{"file read failed", 500, "FILE_READ_FAILED", `{"code":"FILE_READ_FAILED","message":"/x: read failed","source":"DAYTONA_DAEMON"}`, cerr.ExitFailure},
		{"unknown language", 400, "UNSUPPORTED_OPERATION", `{"code":"UNSUPPORTED_OPERATION","message":"unsupported language","source":"DAYTONA_DAEMON"}`, cerr.ExitUsage},
		{"kernel unavailable", 503, "KERNEL_UNAVAILABLE", `{"code":"KERNEL_UNAVAILABLE","message":"kernel failed to start","source":"DAYTONA_DAEMON"}`, cerr.ExitFailure},
		{"git auth", 401, "GIT_AUTH_FAILED", `{"code":"GIT_AUTH_FAILED","message":"git authentication failed","source":"DAYTONA_DAEMON"}`, cerr.ExitAuth},
		{"git repo", 404, "GIT_REPO_NOT_FOUND", `{"code":"GIT_REPO_NOT_FOUND","message":"repository does not exist","source":"DAYTONA_DAEMON"}`, cerr.ExitNotFound},
		{"git branch missing", 404, "GIT_BRANCH_NOT_FOUND", `{"code":"GIT_BRANCH_NOT_FOUND","message":"branch does not exist: x","source":"DAYTONA_DAEMON"}`, cerr.ExitNotFound},
		{"git branch exists", 409, "GIT_BRANCH_EXISTS", `{"code":"GIT_BRANCH_EXISTS","message":"branch already exists: x","source":"DAYTONA_DAEMON"}`, cerr.ExitConflict},
		{"unmerged branch", 409, "CONFLICT", `{"code":"CONFLICT","message":"delete branch failed: error: the branch 'x' is not fully merged","source":"DAYTONA_DAEMON"}`, cerr.ExitConflict},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			resp := &http.Response{StatusCode: tc.status, Body: io.NopCloser(strings.NewReader(tc.body)), Header: http.Header{}}

			err := mapError(http.MethodPost, "/x", resp)

			require.Equal(t, tc.exit, cerr.ExitCode(err))
			require.True(t, IsDaemonCode(err, tc.code))
		})
	}
}

// A file route refusal is the sandbox user's permission, not the credential's.
func TestMapError_fileAccessDeniedHintNamesTheSandboxUser(t *testing.T) {
	t.Parallel()
	resp := &http.Response{
		StatusCode: http.StatusForbidden,
		Body:       io.NopCloser(strings.NewReader(`{"code":"FILE_ACCESS_DENIED","message":"/root/x: permission denied","source":"DAYTONA_DAEMON"}`)),
		Header:     http.Header{},
	}

	err := mapError(http.MethodGet, "/files/download", resp)

	var ce *cerr.Error
	require.ErrorAs(t, err, &ce)
	require.Contains(t, ce.Hint, "sandbox user cannot access that path")
	require.NotContains(t, ce.Hint, "credential")
}
