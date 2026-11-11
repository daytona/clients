// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package api

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

// The image, file, interpreter and git failures the server reports with a
// catalogued code exit with the documented status mapping and keep the code.
func TestServerCodes_exitWithTheStatusMapping(t *testing.T) {
	cases := []struct {
		name   string
		status int
		body   string
		exit   int
	}{
		{"image name taken", 409, `{"code":"IMAGE_NAME_TAKEN","message":"image name 'py' already exists for different content or import parameters","source":"DAYTONA_API"}`, cerr.ExitConflict},
		{"image name reserved", 400, `{"code":"IMAGE_NAME_RESERVED","message":"image name 'img_x' is reserved: 'img_' is a resource id prefix","source":"DAYTONA_API"}`, cerr.ExitUsage},
		{"file access denied", 403, `{"code":"FILE_ACCESS_DENIED","message":"/root/x: permission denied","source":"DAYTONA_DAEMON"}`, cerr.ExitForbidden},
		{"relative path", 400, `{"code":"INVALID_FILE_PATH","message":"path must be absolute","source":"DAYTONA_DAEMON"}`, cerr.ExitUsage},
		{"not a regular file", 400, `{"code":"INVALID_FILE_PATH","message":"/dev/null: not a regular file","source":"DAYTONA_DAEMON"}`, cerr.ExitUsage},
		{"file read failed", 500, `{"code":"FILE_READ_FAILED","message":"/x: read failed","source":"DAYTONA_DAEMON"}`, cerr.ExitFailure},
		{"unknown language", 400, `{"code":"UNSUPPORTED_OPERATION","message":"unsupported language","source":"DAYTONA_DAEMON"}`, cerr.ExitUsage},
		{"kernel unavailable", 503, `{"code":"KERNEL_UNAVAILABLE","message":"kernel failed to start","source":"DAYTONA_DAEMON"}`, cerr.ExitFailure},
		{"git auth", 401, `{"code":"GIT_AUTH_FAILED","message":"git authentication failed","source":"DAYTONA_DAEMON"}`, cerr.ExitAuth},
		{"git repo", 404, `{"code":"GIT_REPO_NOT_FOUND","message":"repository does not exist","source":"DAYTONA_DAEMON"}`, cerr.ExitNotFound},
		{"git branch missing", 404, `{"code":"GIT_BRANCH_NOT_FOUND","message":"branch does not exist: x","source":"DAYTONA_DAEMON"}`, cerr.ExitNotFound},
		{"git branch exists", 409, `{"code":"GIT_BRANCH_EXISTS","message":"branch already exists: x","source":"DAYTONA_DAEMON"}`, cerr.ExitConflict},
		{"unmerged branch", 409, `{"code":"CONFLICT","message":"delete branch failed: error: the branch 'x' is not fully merged","source":"DAYTONA_DAEMON"}`, cerr.ExitConflict},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(tc.body))
			}))
			defer srv.Close()

			err := New(srv.URL, "", "t").Do(context.Background(), http.MethodPost, "/x", nil, nil, nil)

			require.Equal(t, tc.exit, cerr.ExitCode(err))
			var ae *APIError
			require.True(t, errors.As(err, &ae))
			require.Equal(t, tc.status, ae.Status)
			require.True(t, strings.Contains(tc.body, `"code":"`+ae.Code+`"`), ae.Code)
		})
	}
}

// A file route refusal is the sandbox user's permission, not the credential's.
func TestHintFor_fileAccessDeniedNamesTheSandboxUser(t *testing.T) {
	h := hintFor(&APIError{Status: http.StatusForbidden, Code: CodeFileAccessDenied, Source: SourceDaemon})
	require.Contains(t, h, "sandbox user")
	require.NotContains(t, h, "credential")
}
