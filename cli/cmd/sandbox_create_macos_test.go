// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestSandboxCreateMacos_sizesReservationAndPersistence(t *testing.T) {
	created := `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"33333333-3333-3333-3333-333333333333","name":"macbox","status":"pending","wait_cap_seconds":3,"reservation_id":null,"access_token":"mac-create-secret"}`
	stdout, stderr, err := runCLI(t, []string{
		"sandbox", "create-macos", "--image", "base-macos", "--reservation", "region-a:res-1", "--cores", "10", "--memory", "16384", "--disk", "163840", "--persistent", "--secret", "API_KEY", "--no-wait",
	}, []expectedRequest{{
		method:   http.MethodPost,
		path:     "/sandboxes/macos",
		status:   202,
		bodyJSON: `{"image":"base-macos","reservation":"region-a:res-1","cores":10,"memory_mib":16384,"disk_mib":163840,"persistent":true,"secrets":["API_KEY"]}`,
		response: created,
	}, {method: http.MethodGet, path: "/sandboxes/33333333-3333-3333-3333-333333333333", status: 200, response: sandboxDetailFixture("33333333-3333-3333-3333-333333333333", "macos", "pending")}})
	require.NoError(t, err)
	require.JSONEq(t, sandboxDetailFixture("33333333-3333-3333-3333-333333333333", "macos", "pending"), stdout)
	require.NotContains(t, requireJSONStdout(t, stdout), "access_token")
	require.NotContains(t, stdout, "mac-create-secret")
	require.NotContains(t, stderr, "mac-create-secret")

	// Unsized: every axis stays absent; --auto-renew rides with --reservation new.
	_, _, err = runCLI(t, []string{"sandbox", "create-macos", "--yes", "--image", "base-macos", "--reservation", "new", "--auto-renew", "--on-lifecycle-timeout", "delete", "--lifecycle-timeout", "1h", "--no-wait"}, []expectedRequest{{
		method:   http.MethodPost,
		path:     "/sandboxes/macos",
		status:   202,
		bodyJSON: `{"image":"base-macos","reservation":"new","auto_renew":true,"lifecycle_policy":{"timeout_seconds":3600,"on_timeout":"delete"}}`,
		response: created,
	}, {method: http.MethodGet, path: "/sandboxes/33333333-3333-3333-3333-333333333333", status: 200, response: sandboxDetailFixture("33333333-3333-3333-3333-333333333333", "macos", "pending")}})
	require.NoError(t, err)
}

// macOS registers only the flags its request type has.
func TestSandboxCreateMacos_refusesFlagsOfOtherSurfaces(t *testing.T) {
	for _, flag := range [][]string{
		{"--cpu", "2"},
		{"--cores-max", "8"},
		{"--mem-max", "8192"},
		{"--disk-max", "1024"},
		{"--spot"},
		{"--cpu-arch", "arm64"},
		{"--cpu-type", "m4"},
		{"--user", "admin"},
	} {
		args := append([]string{"sandbox", "create-macos", "--image", "base-macos", "--no-wait"}, flag...)
		_, _, err := runCLI(t, args, nil)
		require.Error(t, err, "%v", flag)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), "%v", flag)
	}
}

func TestSandboxCreateMacos_helpIsPublicAndComplete(t *testing.T) {
	parent, _, err := runCLI(t, []string{"sandbox", "--help"}, nil)
	require.NoError(t, err)
	require.Contains(t, parent, "create-macos")

	help, _, err := runCLI(t, []string{"sandbox", "create-macos", "--help"}, nil)
	require.NoError(t, err)
	text := squashSpaces(help)
	for _, line := range []string{
		"--cores N CPU cores, minimum 4; a Mac core is one CPU of the Mac (default: the Mac model's default size)",
		"--disk MIB whole sandbox disk in MiB, macOS included, minimum 81920 (default: the Mac model's default size, or the image's disk size for a captured image)",
		"--memory MIB memory in MiB, minimum 4096 (default: the Mac model's default size)",
		"--reservation ID|new",
		"--auto-renew renew while a sandbox is active; create-macos requires --reservation new",
		"--persistent keep the disk and machine across stop/start on any Mac of the organization, and memory across pause/resume; delete erases the disk. A reservation end or a Mac failure stops a persistent sandbox that has started and never deletes it, though a Mac failure can lose its most recent writes; an ephemeral sandbox ends with its reservation or a failed Mac.",
		"Each omitted size uses the Mac model's default size (DEFAULT_SIZE in mac-model list); a create from a captured image without --disk gets the image's disk size.",
		"--on-lifecycle-timeout ACTION action when --lifecycle-timeout elapses: delete, or stop with --persistent",
		"--on-idle-timeout ACTION action when --idle-timeout elapses: delete, or stop with --persistent",
		"--lifecycle-timeout DURATION wall-clock lifetime (Go duration, e.g. 2h) until --on-lifecycle-timeout fires; re-arms on every start",
		"--timeout DURATION how long to wait for the operation to finish (Go duration); the server keeps working after it (default 10m)",
		"daytona sandbox create-macos --image base-macos",
		"daytona sandbox create-macos --image base-macos --reservation RESERVATION_ID --cores 10 --memory 16384 --disk 163840 --persistent",
		"daytona sandbox create-macos --image base-macos --block-all --allow-domain example.com",
		"--block-all network policy: deny all egress not explicitly allowed",
	} {
		require.Contains(t, text, line)
	}

	help, _, err = runCLI(t, []string{"reservation", "create", "--help"}, nil)
	require.NoError(t, err)
	require.Contains(t, squashSpaces(help), "--model MODEL Mac model slug from mac-model list (default: the region's default model; no upgrade)")
}

func TestSandboxCreateMacos_diskFullIsAConflict(t *testing.T) {
	message := "reservation region-a:res-1 has no free disk space for a new sandbox; delete a macOS sandbox on it, or choose another reservation"
	_, _, err := runCLI(t, []string{"sandbox", "create-macos", "--image", "base-macos", "--reservation", "region-a:res-1", "--no-wait"}, []expectedRequest{{
		method:   http.MethodPost,
		path:     "/sandboxes/macos",
		status:   409,
		bodyJSON: `{"image":"base-macos","reservation":"region-a:res-1"}`,
		response: `{"code":"MAC_DISK_FULL","message":"` + message + `","source":"DAYTONA_API"}`,
	}})
	require.Error(t, err)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
	require.Contains(t, stderrOf(err), message)
}

// The macOS create takes the Linux create's network-policy flags.
func TestSandboxCreateMacos_sendsNetworkPolicy(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "create-macos", "--yes", "--image", "base-macos", "--block-all", "--allow-cidr", "93.184.215.14/32", "--allow-domain", "example.com", "--no-wait"}, []expectedRequest{{
		method:   http.MethodPost,
		path:     "/sandboxes/macos",
		status:   202,
		bodyJSON: `{"image":"base-macos","network_policy":{"block_all":true,"allow_cidrs":["93.184.215.14/32"],"allow_domains":["example.com"]}}`,
		response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"0b0e5a52-31f6-4c2e-9a1e-4c1d1f6a7b02","name":"mac","status":"pending","wait_cap_seconds":3,"reservation_id":null}`,
	}, {method: http.MethodGet, path: "/sandboxes/0b0e5a52-31f6-4c2e-9a1e-4c1d1f6a7b02", status: 200, response: sandboxDetailFixture("0b0e5a52-31f6-4c2e-9a1e-4c1d1f6a7b02", "macos", "pending")}})
	require.NoError(t, err)

	// An allow list without default-deny would leave the sandbox open.
	_, _, err = runCLI(t, []string{"sandbox", "create-macos", "--image", "base-macos", "--allow-cidr", "93.184.215.14/32", "--no-wait"}, nil)
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}
