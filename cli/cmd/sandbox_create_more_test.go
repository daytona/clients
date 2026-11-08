// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"os"
	"path/filepath"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// Without --image the Linux create sends no `image` at all, and the server
// boots the platform default image.
func TestSandboxCreate_linuxWithoutImageOmitsImage(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "create", "--name", "example-user", "--no-wait"}, []expectedRequest{{
		method:   http.MethodPost,
		path:     "/sandboxes",
		status:   202,
		bodyJSON: `{"name":"example-user"}`,
		response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"11111111-1111-1111-1111-111111111111","name":"example-user","status":"pending","wait_cap_seconds":3}`,
	}})
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])

	bodyPath := filepath.Join(t.TempDir(), "create.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"image":null,"memory_mib":256}`), 0o600))
	_, _, err = runCLI(t, []string{"sandbox", "create", "--file", bodyPath, "--no-wait"}, []expectedRequest{{
		method:   http.MethodPost,
		path:     "/sandboxes",
		status:   202,
		bodyJSON: `{"memory_mib":256}`,
		response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"11111111-1111-1111-1111-111111111111","name":"box","status":"pending","wait_cap_seconds":3}`,
	}})
	require.NoError(t, err)
}

// No flags at all is an empty body, not one carrying `"image":""`.
func TestSandboxCreate_linuxWithNoFlagsSendsEmptyBody(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "create", "--no-wait"}, []expectedRequest{{
		method:   http.MethodPost,
		path:     "/sandboxes",
		status:   202,
		bodyJSON: `{}`,
		response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"11111111-1111-1111-1111-111111111111","name":"box","status":"pending","wait_cap_seconds":3}`,
	}})
	require.NoError(t, err)
}

// A blank image from --file names the body field, not a flag the user did not pass.
func TestSandboxCreate_blankFileImageNamesTheField(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "create.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"image":"  "}`), 0o600))
	_, _, err := runCLI(t, []string{"sandbox", "create", "--file", bodyPath, "--no-wait"}, nil)
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.EqualError(t, err, "image must not be empty; omit it to boot the platform default image")

	// A blank --image over a file image still names the flag.
	_, _, err = runCLI(t, []string{"sandbox", "create", "--file", bodyPath, "--image", "", "--no-wait"}, nil)
	require.ErrorContains(t, err, "--image must not be empty")
}

func TestSandboxCreate_blankImageAndMacosStillRequiresImage(t *testing.T) {
	for _, tt := range []struct {
		args    []string
		message string
	}{
		{[]string{"sandbox", "create", "--image", "", "--no-wait"}, "--image must not be empty"},
		{[]string{"sandbox", "create", "--image", "   ", "--no-wait"}, "--image must not be empty"},
		{[]string{"sandbox", "create-macos", "--no-wait"}, "--image is required"},
	} {
		_, _, err := runCLI(t, tt.args, nil)
		require.Error(t, err, "%v", tt.args)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), "%v", tt.args)
		require.ErrorContains(t, err, tt.message, "%v", tt.args)
	}
}

// Each create command accepts only its own request flags and rejects flags
// belonging to another create command before sending a request.
func TestSandboxCreate_perSurfaceFlags(t *testing.T) {
	for _, args := range [][]string{
		// The snapshot is the one positional argument; omitting it is a usage error.
		{"sandbox", "create-from-snapshot"},
		{"sandbox", "create-macos", "--image", "base-macos", "--cpu", "2"},
		{"sandbox", "create-from-snapshot", "snap_abc123", "--cpu", "2"},
		{"sandbox", "create-from-snapshot", "snap_abc123", "--image", "py312"},
	} {
		_, _, err := runCLI(t, args, nil)
		require.Error(t, err, "%v", args)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), "%v", args)
	}
}

func TestSandboxCreate_fileBaseWithFlagOverride(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "create.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"image":"from-file","name":"file-name","memory_mib":256}`), 0o600))
	stdout, _, err := runCLI(t, []string{"sandbox", "create", "--file", bodyPath, "--image", "flag-wins", "--memory", "512", "--no-wait"}, []expectedRequest{{
		method:   http.MethodPost,
		path:     "/sandboxes",
		status:   202,
		bodyJSON: `{"image":"flag-wins","name":"file-name","memory_mib":512}`,
		response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"11111111-1111-1111-1111-111111111111","name":"file-name","status":"pending","wait_cap_seconds":3}`,
	}})
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
}
func TestSandboxCreate_waitAndCreateTTLShape(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "create", "--image", "python:3.12", "--wait", "9s", "--create-ttl", "10m", "--no-wait"}, []expectedRequest{{
		method:   http.MethodPost,
		path:     "/sandboxes",
		status:   202,
		bodyJSON: `{"image":"python:3.12","wait_seconds":9,"create_timeout_seconds":600}`,
		response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"11111111-1111-1111-1111-111111111111","name":"box","status":"pending","wait_cap_seconds":3}`,
	}})
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxCreate_durationKeywordsAndValidation(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "create", "--create-ttl", "max", "--wait", "0s", "--no-wait"}, []expectedRequest{{
		method:   http.MethodPost,
		path:     "/sandboxes",
		status:   202,
		bodyJSON: `{"wait_seconds":0,"create_timeout_seconds":-1}`,
		response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"11111111-1111-1111-1111-111111111111","name":"box","status":"pending","wait_cap_seconds":3}`,
	}})
	require.NoError(t, err)
	for _, args := range [][]string{
		{"--wait", "9"},                  // a unit is required
		{"--create-ttl", "-1"},           // the sentinel is the keyword `max`
		{"--create-ttl", "0s"},           // zero is not a deadline
		{"--idle-timeout", "off"},        // `off` exists on update only
		{"--lifecycle-timeout", "90.5s"}, // whole seconds
		{"--timeout", "3600"},            // --timeout is the local wait, a Go duration
		{"--cores-max", "2"},             // renamed to --cpu-max
		{"--mem-max", "2048"},            // renamed to --memory-max
	} {
		_, _, err := runCLI(t, append([]string{"sandbox", "create", "--no-wait"}, args...), nil)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), args)
	}
}

func TestSandboxCreate_lifecycleActionsFollowTheClass(t *testing.T) {
	for _, args := range [][]string{
		{"sandbox", "create", "--on-lifecycle-timeout", "stop", "--lifecycle-timeout", "1h"},
		{"sandbox", "create", "--on-idle-timeout", "pause", "--idle-timeout", "1h"},
		{"sandbox", "create-from-snapshot", "snap_1", "--on-lifecycle-timeout", "pause", "--lifecycle-timeout", "1h"},
		{"sandbox", "create-macos", "--image", "base-macos", "--reservation", "region-a:res-1", "--on-lifecycle-timeout", "stop", "--lifecycle-timeout", "1h"},
		{"sandbox", "create-macos", "--image", "base-macos", "--reservation", "region-a:res-1", "--persistent", "--on-idle-timeout", "pause", "--idle-timeout", "1h"},
	} {
		_, _, err := runCLI(t, append(args, "--no-wait"), nil)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), args)
	}
	_, _, err := runCLI(t, []string{"sandbox", "create-macos", "--image", "base-macos", "--reservation", "region-a:res-1", "--persistent", "--on-lifecycle-timeout", "stop", "--lifecycle-timeout", "1h", "--no-wait"}, []expectedRequest{{
		method:   http.MethodPost,
		path:     "/sandboxes/macos",
		status:   202,
		bodyJSON: `{"image":"base-macos","reservation":"region-a:res-1","persistent":true,"lifecycle_policy":{"timeout_seconds":3600,"on_timeout":"stop"}}`,
		response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"33333333-3333-3333-3333-333333333333","name":"macbox","status":"pending","wait_cap_seconds":3,"reservation_id":null}`,
	}})
	require.NoError(t, err)
}

func TestSandboxCreate_noWaitReturnsImmediately(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "create", "--image", "python:3.12", "--no-wait"}, []expectedRequest{{
		method:   http.MethodPost,
		path:     "/sandboxes",
		status:   202,
		bodyJSON: `{"image":"python:3.12"}`,
		response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"11111111-1111-1111-1111-111111111111","name":"box","status":"pending","wait_cap_seconds":3}`,
	}})
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxCreateFromSnapshot_linuxRouteAndBody(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "create-from-snapshot", "snap_abc123", "--name", "restored", "--no-wait"}, []expectedRequest{{
		method:   http.MethodPost,
		path:     "/sandboxes/from-snapshot",
		status:   202,
		bodyJSON: `{"snapshot":"snap_abc123","name":"restored"}`,
		response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"11111111-1111-1111-1111-111111111111","name":"restored","status":"pending","wait_cap_seconds":3}`,
	}})
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxCreate_allResourceFlags(t *testing.T) {
	stdout, _, err := runCLI(t, []string{
		"sandbox", "create",
		"--image", "python:3.12",
		"--name", "full",
		"--cpu", "1.5",
		"--memory", "2048",
		"--disk", "4096",
		"--cpu-max", "2.5",
		"--memory-max", "4096",
		"--disk-max", "8192",
		"--cpu-arch", "amd64",
		"--cpu-type", "zen5",
		"--idempotency-key", "idem-1",
		"--region", "region-a",
		"--user", "sandbox",
		"--spot",
		"--port", "8080/tcp",
		"--secret", "db",
		"--allow-domain", "api.example.com",
		"--allow-cidr", "1.2.3.4/32",
		"--lifecycle-timeout", "1h",
		"--on-lifecycle-timeout", "delete",
		"--idle-timeout", "10m",
		"--on-idle-timeout", "delete",
		"--wait", "9s",
		"--create-ttl", "10m",
		"--no-wait",
	}, []expectedRequest{{
		method: http.MethodPost,
		path:   "/sandboxes",
		status: 202,
		check: func(t *testing.T, r *http.Request, body []byte) {
			m := mustJSON(t, string(body))
			assert.Equal(t, "python:3.12", m["image"])
			assert.Equal(t, "full", m["name"])
			assert.EqualValues(t, 2.5, m["cores_max"])
			assert.EqualValues(t, 1.5, m["cpu"])
			assert.EqualValues(t, 4096, m["memory_max_mib"])
			assert.EqualValues(t, 8192, m["disk_max_mib"])
			assert.Equal(t, true, m["spot"])
			assert.Equal(t, "sandbox", m["user"])
			assert.Equal(t, float64(9), m["wait_seconds"])
			assert.Equal(t, float64(600), m["create_timeout_seconds"])
			assert.Equal(t, map[string]any{"timeout_seconds": float64(3600), "on_timeout": "delete", "idle_expiry_seconds": float64(600), "on_idle_timeout": "delete"}, m["lifecycle_policy"])
			_, hasBodyKey := m["idempotency_key"]
			assert.False(t, hasBodyKey)
			assert.Equal(t, "idem-1", r.Header.Get("Idempotency-Key"))
			assert.Equal(t, "region-a", m["region_id"])
			assert.Equal(t, "region-a", r.Header.Get("x-daytona-region"))
			policy := m["network_policy"].(map[string]any)
			assert.Equal(t, []any{"api.example.com"}, policy["allow_domains"])
			assert.Equal(t, []any{"1.2.3.4/32"}, policy["allow_cidrs"])
			_, hasBlockAll := policy["block_all"]
			assert.False(t, hasBlockAll, "omitted --block-all is left for the API to normalize")
		},
		response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"11111111-1111-1111-1111-111111111111","name":"full","status":"pending","wait_cap_seconds":3}`,
	}})
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxCreate_linuxLimitFlags(t *testing.T) {
	_, _, err := runCLI(t, []string{
		"sandbox", "create", "--image", "python:3.12",
		"--cpu", "2", "--cpu-max", "2", "--memory", "4096", "--memory-max", "4096",
		"--disk", "10240", "--disk-max", "10240", "--no-wait",
	}, []expectedRequest{{
		method: http.MethodPost,
		path:   "/sandboxes",
		status: 202,
		check: func(t *testing.T, _ *http.Request, body []byte) {
			m := mustJSON(t, string(body))
			assert.Equal(t, "python:3.12", m["image"])
			assert.EqualValues(t, 2, m["cpu"])
			assert.EqualValues(t, 2, m["cores_max"])
			assert.EqualValues(t, 4096, m["memory_mib"])
			assert.EqualValues(t, 4096, m["memory_max_mib"])
			assert.EqualValues(t, 10240, m["disk_mib"])
			assert.EqualValues(t, 10240, m["disk_max_mib"])
		},
		response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"11111111-1111-1111-1111-111111111111","name":"box","status":"pending","wait_cap_seconds":3}`,
	}})
	require.NoError(t, err)
}

func TestSandboxCreate_fileIdempotencyKeyAlsoSetsHeader(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "create.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"image":"from-file","idempotency_key":"idem-file"}`), 0o600))
	stdout, _, err := runCLI(t, []string{"sandbox", "create", "--file", bodyPath, "--no-wait"}, []expectedRequest{{
		method: http.MethodPost,
		path:   "/sandboxes",
		status: 202,
		check: func(t *testing.T, r *http.Request, body []byte) {
			assert.Equal(t, "idem-file", r.Header.Get("Idempotency-Key"))
			assert.JSONEq(t, `{"image":"from-file","idempotency_key":"idem-file"}`, string(body))
		},
		response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"11111111-1111-1111-1111-111111111111","name":"file-name","status":"pending","wait_cap_seconds":3}`,
	}})
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxCreate_flagIdempotencyKeyOverridesFileBodyKey(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "create.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"image":"from-file","idempotency_key":"idem-file"}`), 0o600))
	stdout, _, err := runCLI(t, []string{"sandbox", "create", "--file", bodyPath, "--idempotency-key", "idem-flag", "--no-wait"}, []expectedRequest{{
		method: http.MethodPost,
		path:   "/sandboxes",
		status: 202,
		check: func(t *testing.T, r *http.Request, body []byte) {
			assert.Equal(t, "idem-flag", r.Header.Get("Idempotency-Key"))
			assert.JSONEq(t, `{"image":"from-file"}`, string(body))
		},
		response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"11111111-1111-1111-1111-111111111111","name":"file-name","status":"pending","wait_cap_seconds":3}`,
	}})
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxCreate_networkPolicyDomainsRejectExplicitBlockAllFalse(t *testing.T) {
	_, _, err := runCLI(t, []string{
		"sandbox", "create", "--image", "python:3.12",
		"--allow-domain", "api.example.com", "--block-all=false",
	}, nil)
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.ErrorContains(t, err, "NETWORK_POLICY_DOMAINS_REQUIRE_BLOCK_ALL")
}

func TestSandboxCreate_cidrOnlyNetworkPolicy(t *testing.T) {
	stdout, _, err := runCLI(t, []string{
		"sandbox", "create", "--image", "python:3.12", "--block-all", "--allow-cidr", "10.1.0.0/16", "--no-wait",
	}, []expectedRequest{
		{
			method: http.MethodPost,
			path:   "/sandboxes",
			status: 202,
			check: func(t *testing.T, r *http.Request, body []byte) {
				m := mustJSON(t, string(body))
				policy := m["network_policy"].(map[string]any)
				assert.Equal(t, true, policy["block_all"])
				assert.Equal(t, []any{"10.1.0.0/16"}, policy["allow_cidrs"])
				_, hasDomains := policy["allow_domains"]
				assert.False(t, hasDomains, "no --allow-domain means allow_domains stays null")
			},
			response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"11111111-1111-1111-1111-111111111111","name":"x","status":"pending","wait_cap_seconds":3}`,
		},
	})
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
}

func TestSandboxCreate_allowCIDRWithoutBlockAllIsRejected(t *testing.T) {
	_, _, err := runCLI(t, []string{
		"sandbox", "create", "--image", "python:3.12", "--allow-cidr", "10.1.0.0/16",
	}, nil)
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.ErrorContains(t, err, "--allow-cidr restricts egress only with --block-all or --allow-domain")
}

func TestSandboxCreate_allowCIDRAcceptsBlockAllFromFile(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "create.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"image":"python:3.12","network_policy":{"block_all":true}}`), 0o600))
	stdout, _, err := runCLI(t, []string{
		"sandbox", "create", "--file", bodyPath, "--allow-cidr", "10.1.0.0/16", "--no-wait",
	}, []expectedRequest{
		{
			method: http.MethodPost,
			path:   "/sandboxes",
			status: 202,
			check: func(t *testing.T, r *http.Request, body []byte) {
				policy := mustJSON(t, string(body))["network_policy"].(map[string]any)
				assert.Equal(t, true, policy["block_all"])
				assert.Equal(t, []any{"10.1.0.0/16"}, policy["allow_cidrs"])
			},
			response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"11111111-1111-1111-1111-111111111111","name":"x","status":"pending","wait_cap_seconds":3}`,
		},
	})
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
}
