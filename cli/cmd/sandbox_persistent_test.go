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

func TestSandboxCreate_sendsPersistence_whenLinux(t *testing.T) {
	for _, value := range []string{"true", "false"} {
		t.Run(value, func(t *testing.T) {
			// Given
			id := "11111111-1111-1111-1111-111111111111"
			// When
			_, _, err := runCLI(t, []string{"sandbox", "create", "--persistent=" + value, "--no-wait"}, []expectedRequest{
				{method: http.MethodPost, path: "/sandboxes", status: 202, bodyJSON: `{"persistent":` + value + `}`, response: `{"name":"box","toolbox_proxy_url":"https://proxy.example/test-tools","id":"` + id + `","status":"pending","wait_cap_seconds":3}`},
				{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "linux", "pending")},
			})
			// Then
			require.NoError(t, err)
		})
	}
}

func TestSandboxCreate_acceptsLifecycle_whenLinuxPersistent(t *testing.T) {
	for _, action := range []string{"stop", "pause"} {
		t.Run(action, func(t *testing.T) {
			// Given
			id := "11111111-1111-1111-1111-111111111111"
			// When
			_, _, err := runCLI(t, []string{"sandbox", "create", "--persistent", "--on-lifecycle-timeout", action, "--lifecycle-timeout", "1h", "--no-wait"}, []expectedRequest{
				{method: http.MethodPost, path: "/sandboxes", status: 202, bodyJSON: `{"persistent":true,"lifecycle_policy":{"timeout_seconds":3600,"on_timeout":"` + action + `"}}`, response: `{"name":"box","toolbox_proxy_url":"https://proxy.example/test-tools","id":"` + id + `","status":"pending","wait_cap_seconds":3}`},
				{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "linux", "pending")},
			})
			// Then
			require.NoError(t, err)
		})
	}
}

func TestSandboxCreate_acceptsPersistenceFromFile_whenLinux(t *testing.T) {
	// Given
	path := filepath.Join(t.TempDir(), "create.json")
	require.NoError(t, os.WriteFile(path, []byte(`{"persistent":true,"lifecycle_policy":{"on_timeout":"pause"}}`), 0o600))
	id := "11111111-1111-1111-1111-111111111111"
	// When
	_, _, err := runCLI(t, []string{"sandbox", "create", "--file", path, "--no-wait"}, []expectedRequest{
		{method: http.MethodPost, path: "/sandboxes", status: 202, bodyJSON: `{"persistent":true,"lifecycle_policy":{"on_timeout":"pause"}}`, response: `{"name":"box","toolbox_proxy_url":"https://proxy.example/test-tools","id":"` + id + `","status":"pending","wait_cap_seconds":3}`},
		{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "linux", "pending")},
	})
	// Then
	require.NoError(t, err)
}

func TestSandboxCreateFromSnapshot_refusesPersistenceOverride(t *testing.T) {
	// Given
	args := []string{"sandbox", "create-from-snapshot", "snap_example", "--persistent", "--no-wait"}
	// When
	_, _, err := runCLI(t, args, nil)
	// Then
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestSandboxCreateFromSnapshot_defersLifecycleSupport_whenPersistenceInherited(t *testing.T) {
	for _, action := range []string{"pause", "stop", "delete"} {
		t.Run(action, func(t *testing.T) {
			// Given
			id := "11111111-1111-1111-1111-111111111111"
			// When
			_, _, err := runCLI(t, []string{"sandbox", "create-from-snapshot", "snap_example", "--on-lifecycle-timeout", action, "--no-wait"}, []expectedRequest{
				{method: http.MethodPost, path: "/sandboxes/from-snapshot", status: 202, bodyJSON: `{"snapshot":"snap_example","lifecycle_policy":{"on_timeout":"` + action + `"}}`, response: `{"name":"box","toolbox_proxy_url":"https://proxy.example/test-tools","id":"` + id + `","status":"pending","wait_cap_seconds":3}`},
				{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "linux", "pending")},
			})
			// Then
			require.NoError(t, err)
		})
	}
}
