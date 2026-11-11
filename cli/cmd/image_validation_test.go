// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestImageLogs_readsResolvedImage_whenReferenceIsName(t *testing.T) {
	for _, follow := range []bool{false, true} {
		t.Run(map[bool]string{false: "read", true: "follow"}[follow], func(t *testing.T) {
			// Given a name that resolves to a different image id.
			const id = "11111111-1111-1111-1111-111111111111"
			args := []string{"image", "logs", "my-image"}
			if follow {
				args = append(args, "--follow")
			}
			// When reading logs through the real command.
			stdout, _, err := runCLIWithResponseReplacement(t, args, []expectedRequest{
				{method: http.MethodGet, path: "/images/my-image", status: 200, response: imageFixture(id, "ready")},
				{method: http.MethodGet, path: "/images/" + id + "/build-logs-url", status: 200, response: `{"url":"__SERVER__/build-logs/images/` + id + `"}`},
				{method: http.MethodGet, path: "/build-logs/images/" + id, status: 200, response: "text/plain:build complete\n"},
			})
			// Then the resolved identity permits the log stream.
			require.NoError(t, err)
			require.Contains(t, stdout, "build complete")
		})
	}
}

func TestImageCreate_rejectsBlankRef_beforeRequest(t *testing.T) {
	for _, value := range []string{"", " \t"} {
		t.Run(value, func(t *testing.T) {
			// Given an explicitly blank registry reference.
			args := []string{"image", "create", "bad", "--ref", value}
			// When importing it.
			_, _, err := runCLI(t, args, nil)
			// Then no request is made and the input is a usage error.
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
		})
	}
}

func TestImageBuild_rejectsMissingDockerfile_beforeRequest(t *testing.T) {
	for _, dockerfile := range []string{"", "build/custom.Dockerfile"} {
		t.Run(dockerfile, func(t *testing.T) {
			// Given a directory without the selected Dockerfile.
			args := []string{"image", "build", "bad", "--context", t.TempDir()}
			if dockerfile != "" {
				args = append(args, "--dockerfile", dockerfile)
			}
			// When building it.
			_, _, err := runCLI(t, args, nil)
			// Then failure occurs before any upload or create request.
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
			require.ErrorContains(t, err, "Dockerfile")
		})
	}
}

func TestImageCreate_reportsBuildError_whenAcceptedBuildFails(t *testing.T) {
	// Given an accepted import whose image records an error and optional code.
	const id = "11111111-1111-1111-1111-111111111111"
	failed := strings.TrimSuffix(imageFixture(id, "failed"), "}") + `,"error":"manifest unavailable","error_code":"BUILD_FAILED"}`
	// When the command waits for that image.
	_, _, err := runCLI(t, []string{"image", "create", "bad", "--ref", "example.com/missing:latest"}, []expectedRequest{
		{method: http.MethodPost, path: "/images", status: 202, bodyJSON: `{"name":"bad","source":{"type":"registry","ref":"example.com/missing:latest"}}`, response: `{"id":"` + id + `","status":"pending"}`},
		{method: http.MethodGet, path: "/images/" + id, status: 200, response: failed},
	})
	// Then the failure identifies its cause without changing its exit code.
	require.Equal(t, cerr.ExitFailure, cerr.ExitCode(err))
	require.ErrorContains(t, err, "manifest unavailable")
	require.ErrorContains(t, err, "BUILD_FAILED")
}

func TestImageBuild_acceptsNestedDockerfile_whenPresent(t *testing.T) {
	// Given a Dockerfile at a custom path inside a local context.
	dir := t.TempDir()
	require.NoError(t, os.Mkdir(filepath.Join(dir, "build"), 0o700))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "build", "custom.Dockerfile"), []byte("FROM scratch\n"), 0o600))
	// When building through the command.
	_, _, err := runCLI(t, []string{"image", "build", "valid", "--context", dir, "--dockerfile", "build/custom.Dockerfile", "--no-wait"}, []expectedRequest{
		{method: http.MethodPost, path: "/images", contentType: "multipart/form-data", status: 202, response: `{"id":"11111111-1111-1111-1111-111111111111","status":"pending"}`, check: func(t *testing.T, request *http.Request, body []byte) {
			parts := readMultipartParts(t, request.Header.Get("Content-Type"), body)
			require.Equal(t, "build/custom.Dockerfile", parts["dockerfile"])
		}},
	})
	// Then the valid context is uploaded.
	require.NoError(t, err)
}
