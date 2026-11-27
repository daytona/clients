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

const upstreamProxyCreated = `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"11111111-1111-1111-1111-111111111111","name":"x","status":"pending","wait_cap_seconds":3}`

func upstreamProxyCreate(t *testing.T, want map[string]any) []expectedRequest {
	t.Helper()
	return []expectedRequest{
		{
			method: http.MethodPost,
			path:   "/sandboxes",
			status: 202,
			check: func(t *testing.T, _ *http.Request, body []byte) {
				require.Equal(t, want, mustJSON(t, string(body))["network_policy"])
			},
			response: upstreamProxyCreated,
		},
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "pending")},
	}
}

func TestSandboxCreate_upstreamProxyFlags(t *testing.T) {
	for _, tc := range []struct {
		name  string
		flags []string
		want  map[string]any
	}{
		{
			"url only",
			[]string{"--upstream-proxy", "http://proxy.example.com:3128"},
			map[string]any{"upstream_proxy": map[string]any{"url": "http://proxy.example.com:3128"}},
		},
		{
			"with credentials",
			[]string{"--upstream-proxy", "https://proxy.example.com", "--upstream-proxy-credentials-secret", "PROXY_AUTH"},
			map[string]any{"upstream_proxy": map[string]any{"url": "https://proxy.example.com", "credentials_secret": "PROXY_AUTH"}},
		},
		{
			"beside the other rules",
			[]string{"--allow-domain", "pypi.org", "--upstream-proxy", "http://proxy.example.com"},
			map[string]any{"allow_domains": []any{"pypi.org"}, "upstream_proxy": map[string]any{"url": "http://proxy.example.com"}},
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			args := append([]string{"sandbox", "create", "--image", "python:3.12", "--no-wait"}, tc.flags...)
			stdout, _, err := runCLI(t, args, upstreamProxyCreate(t, tc.want))
			require.NoError(t, err)
			require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
		})
	}
}

// The flags merge field by field over a --file body, as the other
// network-policy flags do.
func TestSandboxCreate_upstreamProxyCredentialsOverFileURL(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "create.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"image":"python:3.12","network_policy":{"upstream_proxy":{"url":"https://proxy.example.com"}}}`), 0o600))
	_, _, err := runCLI(t, []string{
		"sandbox", "create", "--file", bodyPath, "--upstream-proxy-credentials-secret", "PROXY_AUTH", "--no-wait",
	}, upstreamProxyCreate(t, map[string]any{"upstream_proxy": map[string]any{"url": "https://proxy.example.com", "credentials_secret": "PROXY_AUTH"}}))
	require.NoError(t, err)
}

func TestSandboxCreate_upstreamProxyFromFileIsSent(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "create.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"image":"python:3.12","network_policy":{"upstream_proxy":{"url":"https://proxy.example.com","credentials_secret":"PROXY_AUTH"}}}`), 0o600))
	_, _, err := runCLI(t, []string{"sandbox", "create", "--file", bodyPath, "--upstream-proxy", "https://other.example.com", "--no-wait"},
		upstreamProxyCreate(t, map[string]any{"upstream_proxy": map[string]any{"url": "https://other.example.com", "credentials_secret": "PROXY_AUTH"}}))
	require.NoError(t, err)
}

func TestSandboxCreate_upstreamProxyUsageErrors(t *testing.T) {
	for _, tc := range []struct {
		flags   []string
		message string
	}{
		{[]string{"--upstream-proxy-credentials-secret", "PROXY_AUTH"}, "--upstream-proxy-credentials-secret needs --upstream-proxy"},
		{[]string{"--upstream-proxy", " "}, "--upstream-proxy must not be blank"},
		{[]string{"--upstream-proxy", "https://proxy.example.com", "--upstream-proxy-credentials-secret", ""}, "--upstream-proxy-credentials-secret must not be blank"},
	} {
		args := append([]string{"sandbox", "create", "--image", "python:3.12", "--no-wait"}, tc.flags...)
		_, _, err := runCLI(t, args, nil)
		require.Error(t, err, "%v", tc.flags)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), "%v", tc.flags)
		require.ErrorContains(t, err, tc.message)
	}
}

// A misspelled key inside upstream_proxy in --file is refused before any request.
func TestSandboxCreate_upstreamProxyFileKeysAreExact(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "create.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"image":"python:3.12","network_policy":{"upstream_proxy":{"url":"https://proxy.example.com","credentialsSecret":"PROXY_AUTH"}}}`), 0o600))
	_, _, err := runCLI(t, []string{"sandbox", "create", "--file", bodyPath, "--no-wait"}, nil)
	require.Error(t, err)
	require.ErrorContains(t, err, "credentialsSecret")
}
