// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"encoding/json"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestToolboxCP_downloadsSingleFile(t *testing.T) {
	dir := t.TempDir()
	dst := filepath.Join(dir, "out.txt")
	_, _, err := runToolboxCLI(t, []string{"sandbox", "cp", "sbx:/tmp/demo.txt", dst}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodPost, path: "/sandboxes/sbx/files/sign", status: 200, auth: true, response: signedFileURLFixture(t, "GET", "/tmp/demo.txt"), check: expectSignFileRequest("GET", "/tmp/demo.txt")},
		{method: http.MethodGet, path: "/test-tools/sbx/files/download", status: 200, auth: false, responseContentType: "application/octet-stream", response: `payload`, check: expectSignedTransferQuery("/tmp/demo.txt")},
	})
	require.NoError(t, err)
	data, readErr := os.ReadFile(dst)
	require.NoError(t, readErr)
	require.Equal(t, "payload", string(data))
}

func TestToolboxCP_uploadsSingleFile(t *testing.T) {
	src := filepath.Join(t.TempDir(), "in.txt")
	require.NoError(t, os.WriteFile(src, []byte("payload"), 0o600))
	_, _, err := runToolboxCLI(t, []string{"sandbox", "cp", src, "sbx:/tmp/demo.txt"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodGet, path: "/test-tools/sbx/files/info", status: 404, auth: true, response: `{"code":"FILE_NOT_FOUND","message":"no such file"}`, check: func(t *testing.T, r *http.Request, _ []byte) {
			assert.Equal(t, "/tmp/demo.txt", r.URL.Query().Get("path"))
		}},
		{method: http.MethodPost, path: "/sandboxes/sbx/files/sign", status: 200, auth: true, response: signedFileURLFixture(t, "POST", "/tmp/demo.txt"), check: expectSignFileRequest("POST", "/tmp/demo.txt")},
		{method: http.MethodPost, path: "/test-tools/sbx/files/upload", status: 200, auth: false, check: func(t *testing.T, r *http.Request, body []byte) {
			expectSignedTransferQuery("/tmp/demo.txt")(t, r, body)
			_, params, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
			assert.NoError(t, err)
			reader := multipart.NewReader(bytes.NewReader(body), params["boundary"])
			part, partErr := reader.NextPart()
			assert.NoError(t, partErr)
			content, readErr := io.ReadAll(part)
			assert.NoError(t, readErr)
			assert.Equal(t, "file", part.FormName())
			assert.Equal(t, "payload", string(content))
		}},
	})
	require.NoError(t, err)
}

func TestRecursiveDownloadPath(t *testing.T) {
	existingDir := t.TempDir()
	into, err := recursiveDownloadPath("/remote/work", existingDir)
	require.NoError(t, err)
	require.Equal(t, filepath.Join(existingDir, "work"), into)

	trailingBase := filepath.Join(t.TempDir(), "dest")
	trailing, err := recursiveDownloadPath("/remote/work", trailingBase+string(filepath.Separator))
	require.NoError(t, err)
	require.Equal(t, filepath.Join(trailingBase, "work"), trailing)

	exactBase := filepath.Join(t.TempDir(), "renamed")
	exact, err := recursiveDownloadPath("/remote/work", exactBase)
	require.NoError(t, err)
	require.Equal(t, exactBase, exact)
}

func parseInt64(t *testing.T, value string) int64 {
	t.Helper()
	parsed, err := strconv.ParseInt(value, 10, 64)
	require.NoError(t, err)
	return parsed
}

// The fixture the mocked `POST …/files/sign` answers with: the contract's
// five fields, `url` on the test server's toolbox base (`__SERVER__` is
// substituted by the harness), signed with a fixed test signature.
// A signed file URL's expiry lies in the future.
var signedFileTestExpires = time.Now().Add(time.Hour).UTC()

const (
	signedFileTestSignature = "v1_test-signature"
)

func signedFileURLFixture(t *testing.T, method, remotePath string) string {
	t.Helper()
	route := "/files/download"
	if method == http.MethodPost {
		route = "/files/upload"
	}
	payload := map[string]any{
		"url":        "__SERVER__/test-tools/sbx" + route + "?path=" + url.QueryEscape(remotePath) + "&expires=" + strconv.FormatInt(signedFileTestExpires.Unix(), 10) + "&signature=" + signedFileTestSignature,
		"method":     method,
		"path":       remotePath,
		"expires_at": signedFileTestExpires.Format(time.RFC3339),
		"signature":  signedFileTestSignature,
	}
	encoded, err := json.Marshal(payload)
	require.NoError(t, err)
	return string(encoded)
}

// expectSignFileRequest asserts the sign request body is the contract's
// `SignSandboxFileUrlRequest`: the method and sandbox path verbatim, and an absolute
// `expires_at` within the CLI's one-hour transfer ttl of now.
func expectSignFileRequest(method, remotePath string) func(*testing.T, *http.Request, []byte) {
	return func(t *testing.T, _ *http.Request, body []byte) {
		var payload map[string]any
		if !assert.NoError(t, json.Unmarshal(body, &payload)) {
			return
		}
		assert.Equal(t, method, payload["method"])
		assert.Equal(t, remotePath, payload["path"])
		expiresAt, ok := payload["expires_at"].(string)
		if assert.True(t, ok, "expires_at must be an RFC3339 string: %v", payload["expires_at"]) {
			expires, err := time.Parse(time.RFC3339, expiresAt)
			if assert.NoError(t, err) {
				now := time.Now()
				assert.True(t, expires.After(now))
				assert.True(t, !expires.After(now.Add(presignedTransferTTL+time.Second)))
			}
		}
		assert.Len(t, payload, 3)
	}
}

// expectSignedTransferQuery asserts the transfer hit the proxy with the
// server-minted link verbatim: the signed path, expiry, and signature from the
// sign response, and no bearer (the signature is the credential).
func expectSignedTransferQuery(remotePath string) func(*testing.T, *http.Request, []byte) {
	return func(t *testing.T, r *http.Request, _ []byte) {
		assert.Equal(t, remotePath, r.URL.Query().Get("path"))
		assert.Equal(t, strconv.FormatInt(signedFileTestExpires.Unix(), 10), r.URL.Query().Get("expires"))
		assert.Equal(t, signedFileTestSignature, r.URL.Query().Get("signature"))
	}
}
