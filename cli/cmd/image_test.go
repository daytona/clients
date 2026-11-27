// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"github.com/daytona/clients/cli/internal/cerr"
)

func TestImageCommands_routeAndRenderJSON(t *testing.T) {
	tests := []struct {
		name     string
		args     []string
		expected []expectedRequest
	}{
		{
			name: "import image waits for ready",
			args: []string{"image", "create", "img", "--ref", "python:3.12"},
			expected: []expectedRequest{
				{method: http.MethodPost, path: "/images", status: 202, bodyJSON: `{"name":"img","source":{"type":"registry","ref":"python:3.12"}}`, response: `{"id":"11111111-1111-1111-1111-111111111111","status":"pending"}`},
				{method: http.MethodGet, path: "/images/11111111-1111-1111-1111-111111111111", query: "wait=ready&timeout_ms=55000", status: 200, response: imageFixture("11111111-1111-1111-1111-111111111111", "ready")},
			},
		},
		{
			name:     "import image",
			args:     []string{"image", "create", "img", "--ref", "python:3.12", "--no-wait"},
			expected: []expectedRequest{{method: http.MethodPost, path: "/images", status: 202, bodyJSON: `{"name":"img","source":{"type":"registry","ref":"python:3.12"}}`, response: `{"id":"11111111-1111-1111-1111-111111111111","status":"pending"}`}},
		},
		{
			name: "list images",
			args: []string{"image", "list", "--limit", "1"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/images/search", query: "limit=1", status: 200, response: withSearchTotals(t, imagePageFixture("11111111-1111-1111-1111-111111111111"))},
			},
		},
		{
			name: "list images without search",
			args: []string{"image", "list", "--limit", "1"},
			expected: []expectedRequest{
				searchNotConfiguredRequest("/images/search"),
				{method: http.MethodGet, path: "/regions", status: 200, response: `{"regions":[],"default_region_id":null}`},
				{method: http.MethodGet, path: "/images", query: "limit=1", status: 200, response: imagePageFixture("11111111-1111-1111-1111-111111111111")},
			},
		},
		{
			name: "list images with an empty name filter",
			args: []string{"image", "list", "--name", "", "--limit", "1"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/images/search", query: "limit=1&name=", status: 200, response: withSearchTotals(t, `{"images":[],"next_cursor":null}`)},
			},
		},
		{
			name: "list images with an empty name filter without search",
			args: []string{"image", "list", "--name", "", "--limit", "1"},
			expected: []expectedRequest{
				searchNotConfiguredRequest("/images/search"),
				{method: http.MethodGet, path: "/regions", status: 200, response: `{"regions":[],"default_region_id":null}`},
				{method: http.MethodGet, path: "/images", query: "limit=1&name=", status: 200, response: `{"images":[],"next_cursor":null}`},
			},
		},
		{
			name: "list platform images",
			args: []string{"image", "list", "--platform", "true", "--limit", "1"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/images/search", query: "limit=1&platform=true", status: 200, response: withSearchTotals(t, imagePageFixture("11111111-1111-1111-1111-111111111111"))},
			},
		},
		{
			name: "list platform images without search",
			args: []string{"image", "list", "--platform", "true", "--limit", "1"},
			expected: []expectedRequest{
				searchNotConfiguredRequest("/images/search"),
				{method: http.MethodGet, path: "/regions", status: 200, response: `{"regions":[],"default_region_id":null}`},
				{method: http.MethodGet, path: "/images", query: "limit=1&platform=true", status: 200, response: imagePageFixture("11111111-1111-1111-1111-111111111111")},
			},
		},
		{
			name:     "get image",
			args:     []string{"image", "get", "11111111-1111-1111-1111-111111111111"},
			expected: []expectedRequest{{method: http.MethodGet, path: "/images/11111111-1111-1111-1111-111111111111", status: 200, response: imageFixture("11111111-1111-1111-1111-111111111111", "ready")}},
		},
		{
			name:     "import an anonymous image with an idle expiry",
			args:     []string{"image", "create", "--ref", "python:3.12", "--idle-expiry", "24h", "--no-wait"},
			expected: []expectedRequest{{method: http.MethodPost, path: "/images", status: 202, bodyJSON: `{"idle_expiry_seconds":86400,"source":{"type":"registry","ref":"python:3.12"}}`, response: `{"id":"11111111-1111-1111-1111-111111111111","status":"pending"}`}},
		},
		{
			name: "capture an anonymous image with the longest idle expiry",
			args: []string{"image", "capture", "22222222-2222-2222-2222-222222222222", "--idle-expiry", "max", "--no-wait"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "linux", "running")},
				{method: http.MethodPost, path: "/sandboxes/22222222-2222-2222-2222-222222222222/images", status: 202, bodyJSON: `{"idle_expiry_seconds":-1}`, response: `{"id":"11111111-1111-1111-1111-111111111111","status":"pending"}`},
			},
		},
		{
			name:     "promote image",
			args:     []string{"image", "promote", "11111111-1111-1111-1111-111111111111", "keep-this"},
			expected: []expectedRequest{{method: http.MethodPatch, path: "/images/11111111-1111-1111-1111-111111111111", status: 200, bodyJSON: `{"name":"keep-this"}`, response: imageFixture("11111111-1111-1111-1111-111111111111", "ready")}},
		},
		{
			name: "delete image",
			// One request: the API resolves an id or a name, and the
			// acknowledgement repeats the reference as given.
			args:     []string{"image", "delete", "py312", "--yes"},
			expected: []expectedRequest{{method: http.MethodDelete, path: "/images/py312", status: 204}},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			stdout, _, err := runCLI(t, tt.args, tt.expected)
			require.NoError(t, err)
			require.NotEmpty(t, requireJSONStdout(t, stdout))
			if tt.name == "delete image" {
				require.True(t, isAck(t, stdout, "ref", "py312", "deleted"), stdout)
			}
		})
	}
}

func TestImageLogs_discoversRegionalURL(t *testing.T) {
	stdout, _, err := runCLIWithResponseReplacement(t, []string{"image", "logs", "11111111-1111-1111-1111-111111111111"}, []expectedRequest{
		{method: http.MethodGet, path: "/images/11111111-1111-1111-1111-111111111111", status: 200, response: imageFixture("11111111-1111-1111-1111-111111111111", "ready")},
		{method: http.MethodGet, path: "/images/11111111-1111-1111-1111-111111111111/build-logs-url", status: 200, response: `{"url":"__SERVER__/build-logs/images/11111111-1111-1111-1111-111111111111"}`},
		{method: http.MethodGet, path: "/build-logs/images/11111111-1111-1111-1111-111111111111", query: "follow=false&format=text", status: 200, response: "text/plain:state: ready\n"},
	})
	require.NoError(t, err)
	require.Contains(t, stdout, "state: ready")
}

func runCLIWithResponseReplacement(t *testing.T, args []string, expected []expectedRequest) (string, string, error) {
	t.Helper()
	var index int
	serverURL := ""
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if index >= len(expected) {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		exp := expected[index]
		index++
		require.Equal(t, exp.method, r.Method)
		require.Equal(t, exp.path, r.URL.Path)
		require.Equal(t, "Bearer k", r.Header.Get("Authorization"))
		require.Equal(t, "cli", r.Header.Get("X-Daytona-Source"))
		if exp.contentType != "" {
			require.Contains(t, r.Header.Get("Content-Type"), exp.contentType)
		}
		if exp.query != "" {
			requireQueryEqual(t, exp.query, r.URL.RawQuery)
		}
		body, err := io.ReadAll(r.Body)
		require.NoError(t, err)
		if exp.bodyJSON != "" {
			require.JSONEq(t, exp.bodyJSON, string(body))
		} else if exp.check == nil {
			require.Empty(t, strings.TrimSpace(string(body)))
		}
		if exp.check != nil {
			exp.check(t, r, body)
		}
		response := strings.ReplaceAll(exp.response, "__SERVER__", serverURL)
		if response != "" {
			if strings.HasPrefix(response, "text/plain:") {
				w.Header().Set("Content-Type", "text/plain")
				w.WriteHeader(exp.status)
				_, _ = io.WriteString(w, strings.TrimPrefix(response, "text/plain:"))
				return
			}
			w.Header().Set("Content-Type", "application/json")
		}
		w.WriteHeader(exp.status)
		_, _ = io.WriteString(w, response)
	}))
	defer server.Close()
	serverURL = server.URL

	t.Setenv("DAYTONA_CONFIG_DIR", t.TempDir())
	for _, key := range []string{"DAYTONA_API_URL", "DAYTONA_V2_API_URL", "DAYTONA_API_KEY", "DAYTONA_PROFILE", "DAYTONA_ORG", "DAYTONA_PROJECT", "DAYTONA_REGION", "DAYTONA_OIDC_CLIENT_ID", "DAYTONA_YES"} {
		t.Setenv(key, "")
	}
	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	root := NewRoot(stdout, stderr)
	root.SetArgs(append([]string{"--api-url", server.URL, "--api-key", "k", "-o", "json"}, args...))
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	err := root.ExecuteContext(ctx)
	if err == nil {
		require.Equal(t, len(expected), index)
	}
	return stdout.String(), stderr.String(), err
}

// A named image is permanent: --idle-expiry beside a name is refused before
// any request, whether the name comes from the argument, --name or --file.
func TestImageIdleExpiryNeedsAnAnonymousImage(t *testing.T) {
	body := filepath.Join(t.TempDir(), "body.json")
	require.NoError(t, os.WriteFile(body, []byte(`{"name":"from-file","source":{"type":"registry","ref":"python:3.12"}}`), 0o600))
	for _, args := range [][]string{
		{"image", "create", "named", "--ref", "python:3.12", "--idle-expiry", "1h"},
		{"image", "create", "--file", body, "--idle-expiry", "1h"},
		{"image", "capture", "22222222-2222-2222-2222-222222222222", "--name", "kept", "--idle-expiry", "1h"},
		{"image", "capture", "22222222-2222-2222-2222-222222222222", "--idle-expiry", "1.5s"},
	} {
		_, _, err := runCLI(t, args, nil)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), "%v: %v", args, err)
	}
}
