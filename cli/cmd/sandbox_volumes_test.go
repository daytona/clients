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

func TestSandboxCreate_sendsVolumesFromFlagsAndFile(t *testing.T) {
	for _, surface := range []string{"create", "create-macos"} {
		for _, source := range []string{"flag", "file", "override"} {
			t.Run(surface+"/"+source, func(t *testing.T) {
				// Given a mount, supplied as flags or JSON (flags override JSON).
				args := []string{"sandbox", surface, "--image", "base", "--yes", "--no-wait"}
				if source != "flag" {
					mount := "/data"
					if source == "override" {
						mount = "/old"
					}
					path := filepath.Join(t.TempDir(), "create.json")
					require.NoError(t, os.WriteFile(path, []byte(`{"volumes":[{"volume":"data","mount_path":"`+mount+`","subpath":"project"}]}`), 0o600))
					args = append(args, "--file", path)
				}
				if source != "file" {
					args = append(args, "--volume", "data:/data:project")
				}
				path := "/sandboxes"
				if surface == "create-macos" {
					path += "/macos"
				}
				// When the create command executes.
				class := "linux"
				if surface == "create-macos" {
					class = "macos"
				}
				_, _, err := runCLI(t, args, []expectedRequest{{
					method: http.MethodPost, path: path, status: 202,
					bodyJSON: `{"image":"base","volumes":[{"volume":"data","mount_path":"/data","subpath":"project"}]}`,
					response: `{"toolbox_proxy_url":"https://proxy.example","id":"11111111-1111-1111-1111-111111111111","name":"box","status":"pending","wait_cap_seconds":3}`,
				}, {
					method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200,
					response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", class, "pending"),
				}})
				// Then the mount reaches the create request on each supported surface.
				require.NoError(t, err)
			})
		}
	}
}

func TestSandboxCreate_sendsReadOnlyVolume(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "create", "--volume", "data:/data:ro", "--no-wait"}, []expectedRequest{{
		method: http.MethodPost, path: "/sandboxes", status: 202,
		bodyJSON: `{"volumes":[{"volume":"data","mount_path":"/data","read_only":true}]}`,
		response: `{"toolbox_proxy_url":"https://proxy.example","id":"11111111-1111-1111-1111-111111111111","name":"box","status":"pending","wait_cap_seconds":3}`,
	}, {
		method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200,
		response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "pending"),
	}})
	require.NoError(t, err)
}

func TestSandboxCreateFromSnapshot_rejectsVolumeField(t *testing.T) {
	path := filepath.Join(t.TempDir(), "create.json")
	require.NoError(t, os.WriteFile(path, []byte(`{"volumes":null}`), 0o600))
	_, _, err := runCLI(t, []string{"sandbox", "create-from-snapshot", "saved", "--file", path}, nil)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.ErrorContains(t, err, "volumes is not supported")
}
