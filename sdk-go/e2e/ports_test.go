// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build e2e

package e2e_test

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	daytona "go.daytona.com/sdk/v2"
)

// rawPortsToolbox sends raw bytes to a sandbox's toolbox, independently of
// SDK toolbox services. It accepts any 2xx response and returns its bounded body.
func rawPortsToolbox(ctx context.Context, t *testing.T, sandboxID, method, path string, body []byte) []byte {
	t.Helper()
	var detail struct {
		ToolboxProxyURL string `json:"toolbox_proxy_url"`
	}
	require.NoError(t, rawAPI(ctx, http.MethodGet, "/sandboxes/"+url.PathEscape(sandboxID), nil, &detail))
	var credential struct {
		AccessToken string `json:"access_token"`
	}
	require.NoError(t, rawAPI(ctx, http.MethodPost, "/sandboxes/"+url.PathEscape(sandboxID)+"/access-token", []byte(`{}`), &credential))
	require.NotEmpty(t, credential.AccessToken)
	base := strings.TrimRight(detail.ToolboxProxyURL, "/")
	require.NotEmpty(t, base)
	if strings.Contains(base, "{sandboxId}") || strings.Contains(base, "{sandbox_id}") {
		base = strings.NewReplacer("{sandboxId}", url.PathEscape(sandboxID), "{sandbox_id}", url.PathEscape(sandboxID)).Replace(base)
	} else {
		base += "/" + url.PathEscape(sandboxID)
	}
	request, err := http.NewRequestWithContext(ctx, method, base+path, bytes.NewReader(body))
	require.NoError(t, err)
	request.Header.Set("Authorization", "Bearer "+credential.AccessToken)
	if body != nil {
		contentType := "application/octet-stream"
		if strings.HasPrefix(path, "/processes") {
			contentType = "application/json"
		}
		request.Header.Set("Content-Type", contentType)
	}
	client := &http.Client{Timeout: time.Minute, CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
		return http.ErrUseLastResponse
	}}
	response, err := client.Do(request)
	require.NoError(t, err)
	defer func() { require.NoError(t, response.Body.Close()) }()
	data, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	require.NoError(t, err)
	// Do not put response bodies or credential-bearing URLs into test failures.
	require.GreaterOrEqual(t, response.StatusCode, 200)
	require.Less(t, response.StatusCode, 300)
	return data
}

// portsExecRaw runs a shell command over raw HTTP, waits at most 30 seconds,
// and fails the test unless the process exits successfully.
func portsExecRaw(t *testing.T, ctx context.Context, sandboxID, command string) {
	t.Helper()
	body, err := json.Marshal(struct {
		ShellCommand string `json:"shellCommand"`
		KeepLogs     string `json:"keepLogs"`
	}{ShellCommand: command, KeepLogs: "on_exit_ttl"})
	require.NoError(t, err)
	raw := rawPortsToolbox(ctx, t, sandboxID, http.MethodPost, "/processes", body)
	var process struct {
		ID string `json:"id"`
	}
	require.NoError(t, json.Unmarshal(raw, &process))
	require.NotEmpty(t, process.ID)
	raw = rawPortsToolbox(ctx, t, sandboxID, http.MethodGet,
		"/processes/"+url.PathEscape(process.ID)+"/wait?timeoutMs=30000", nil)
	var result struct {
		ExitCode *int   `json:"exitCode"`
		Reason   string `json:"reason"`
	}
	require.NoError(t, json.Unmarshal(raw, &result))
	require.Equal(t, "exited", result.Reason)
	require.NotNil(t, result.ExitCode)
	require.Zero(t, *result.ExitCode)
}

func TestPortsLive(t *testing.T) {
	// Given: a server bound to port 8080 in a uniquely named, cleanup-owned sandbox.
	client := newClient(t)
	ctx := testContext(t)
	id := rawSandbox(t, uniqueName(t, "gosdk-ports"))
	sandbox, err := client.Get(ctx, id, nil)
	require.NoError(t, err)
	python := "import http.server; http.server.HTTPServer(('0.0.0.0',8080),type('H',(http.server.BaseHTTPRequestHandler,),{'do_GET':lambda s:(s.send_response(200),s.end_headers(),s.wfile.write(b'gosdk-ports-ready'))})).serve_forever()"
	portsExecRaw(t, ctx, id, "nohup python3 -u -c "+strconv.Quote(python)+
		" >/tmp/gosdk-ports-http.log 2>&1 </dev/null &")
	eventually(ctx, t, 30*time.Second, func() (bool, error) {
		return sandbox.Ports().IsInUse(ctx, 8080)
	})

	t.Run("listeners", func(t *testing.T) {
		// When
		page, listErr := sandbox.Ports().List(ctx)
		// Then
		require.NoError(t, listErr)
		require.Contains(t, page.Items, 8080)
	})
	t.Run("public preview without credentials", func(t *testing.T) {
		// When
		endpoint, exposeErr := sandbox.Ports().Expose(ctx, 8080, &daytona.ExposePortOptions{Public: true})
		require.NoError(t, exposeErr)
		link, linkErr := sandbox.Ports().PreviewLink(ctx, 8080)
		require.NoError(t, linkErr)
		// Then
		require.True(t, endpoint.Public)
		require.Empty(t, link.Token)
		require.Equal(t, endpoint.URL, link.URL)
		eventually(ctx, t, 45*time.Second, func() (bool, error) {
			return portsPreviewResponds(ctx, link.URL, "")
		})
	})
	t.Run("endpoint page", func(t *testing.T) {
		// When
		page, listErr := sandbox.Ports().ListEndpoints(ctx, &daytona.ListEndpointsOptions{Limit: 1})
		// Then
		require.NoError(t, listErr)
		require.NotEmpty(t, page.Items)
		require.Equal(t, 8080, page.Items[0].Port)
	})
	t.Run("signed preview", func(t *testing.T) {
		// When
		link, linkErr := sandbox.Ports().SignedPreviewLink(ctx, 8080, &daytona.SignedPreviewLinkOptions{ExpiresIn: 5 * time.Minute})
		// Then
		require.NoError(t, linkErr)
		require.NotEmpty(t, link.Token)
		require.False(t, link.ExpiresAt.IsZero())
		eventually(ctx, t, 30*time.Second, func() (bool, error) {
			return portsPreviewResponds(ctx, link.URL, "")
		})
	})
	t.Run("private preview", func(t *testing.T) {
		// When
		_, exposeErr := sandbox.Ports().Expose(ctx, 8080, nil)
		require.NoError(t, exposeErr)
		link, linkErr := sandbox.Ports().PreviewLink(ctx, 8080)
		// Then
		require.NoError(t, linkErr)
		require.NotEmpty(t, link.Token)
		eventually(ctx, t, 45*time.Second, func() (bool, error) {
			return portsPreviewResponds(ctx, link.URL, link.Token)
		})
	})
	t.Run("revoke", func(t *testing.T) {
		// When
		revokeErr := sandbox.Ports().ExpireSignedPreviewLinks(ctx)
		// Then
		require.NoError(t, revokeErr)
	})
	t.Run("unexpose", func(t *testing.T) {
		// When
		unexposeErr := sandbox.Ports().Unexpose(ctx, 8080)
		// Then
		require.NoError(t, unexposeErr)
	})
}

func portsPreviewResponds(ctx context.Context, address, token string) (ready bool, err error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, address, nil)
	if err != nil {
		return false, err
	}
	request.Header.Set(daytona.PreviewWarningSkipHeader, daytona.PreviewWarningSkipHeaderValue)
	if token != "" {
		request.Header.Set("X-Daytona-Preview-Token", token)
	}
	client := &http.Client{Timeout: 10 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
		return http.ErrUseLastResponse
	}}
	response, err := client.Do(request)
	if err != nil {
		// Startup DNS/routing propagation is eventual; keep credential-bearing
		// URL errors out of diagnostics and let the caller's deadline bound it.
		return false, nil
	}
	defer func() {
		if closeErr := response.Body.Close(); err == nil {
			err = closeErr
		}
	}()
	data, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil {
		return false, err
	}
	return response.StatusCode == http.StatusOK && string(data) == "gosdk-ports-ready", nil
}
