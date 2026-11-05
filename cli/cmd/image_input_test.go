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

func TestImageCapture_sendsNameWhenProvided(t *testing.T) {
	for _, named := range []bool{false, true} {
		t.Run(map[bool]string{false: "anonymous", true: "named"}[named], func(t *testing.T) {
			// Given a sandbox and an optional image name.
			const id = "11111111-1111-1111-1111-111111111111"
			args := []string{"image", "capture", id, "--no-wait"}
			body := `{}`
			if named {
				args = append(args, "--name", "saved-image")
				body = `{"name":"saved-image"}`
			}
			// When capturing the image through the command's HTTP boundary.
			stdout, _, err := runCLI(t, args, []expectedRequest{
				{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "linux", "running")},
				{method: http.MethodPost, path: "/sandboxes/" + id + "/images", status: 202, bodyJSON: body, response: `{"id":"img-example","status":"pending"}`},
			})
			// Then the request is contract-valid and the operation is returned.
			require.NoError(t, err)
			require.Equal(t, "img-example", requireJSONStdout(t, stdout)["id"])
		})
	}
}

func TestImageCommands_rejectUnknownFileFields(t *testing.T) {
	for _, command := range []string{"create", "build"} {
		t.Run(command, func(t *testing.T) {
			// Given an image request file with an unknown field.
			bodyPath := filepath.Join(t.TempDir(), "image.json")
			require.NoError(t, os.WriteFile(bodyPath, []byte(`{"source":{"type":"registry","ref":"python:3.12"},"unknown_field":true}`), 0o600))
			if command == "build" {
				require.NoError(t, os.WriteFile(bodyPath, []byte(`{"context":"context.tar","unknown_field":true}`), 0o600))
			}
			// When the CLI parses the request without contacting the API.
			_, _, err := runCLI(t, []string{"image", command, "img", "--file", bodyPath}, nil)
			// Then the unknown input is a usage error.
			require.Error(t, err)
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
			require.ErrorContains(t, err, "unknown_field")
		})
	}
}

func TestImageBuild_rejectsNonStringFileFields(t *testing.T) {
	for _, field := range []string{"cpu_arch", "region_id", "dockerfile", "context", "name"} {
		t.Run(field, func(t *testing.T) {
			// Given a build file whose string field has another JSON type.
			bodyPath := filepath.Join(t.TempDir(), "image.json")
			body := `{"context":"context.tar","` + field + `":64}`
			if field == "context" {
				body = `{"context":64}`
			}
			require.NoError(t, os.WriteFile(bodyPath, []byte(body), 0o600))
			// When the CLI parses the request without contacting the API.
			_, _, err := runCLI(t, []string{"image", "build", "img", "--file", bodyPath}, nil)
			// Then the malformed input is a usage error, not a silently dropped field.
			require.Error(t, err)
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
			require.ErrorContains(t, err, field)
		})
	}
}
