// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"archive/tar"
	"bytes"
	"encoding/base64"
	"encoding/json"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestToolboxCP_rejectsBothRemote(t *testing.T) {
	_, _, err := runToolboxCLI(t, []string{"sandbox", "cp", "sbx:/a", "sbx:/b"}, nil)
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestToolboxCP_rejectsNeitherRemote(t *testing.T) {
	_, _, err := runToolboxCLI(t, []string{"sandbox", "cp", "/tmp/a", "/tmp/b"}, nil)
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestDestinationUploadPath_trailingSlash(t *testing.T) {
	require.Equal(t, "/remote/file.txt", destinationUploadPath("/local/file.txt", "/remote/"))
	// The local side is an OS path: on Windows `path.Base` would keep the
	// whole drive path as the remote file name.
	require.Equal(t, "/remote/"+filepath.Base(filepath.Join("dir", "report.pdf")), destinationUploadPath(filepath.Join("dir", "report.pdf"), "/remote/"))
}

func TestDestinationUploadPath_explicitFile(t *testing.T) {
	require.Equal(t, "/remote/out.txt", destinationUploadPath("/local/file.txt", "/remote/out.txt"))
}

func TestToolboxCP_recursiveUploadUsesSignedArchiveUploadThenRemoteExtract(t *testing.T) {
	tempDir := t.TempDir()
	localDir := filepath.Join(tempDir, "src")
	require.NoError(t, os.MkdirAll(localDir, 0o755))
	require.NoError(t, os.WriteFile(filepath.Join(localDir, "demo.txt"), []byte("payload"), 0o600))
	oldTempPath := newSandboxArchiveTempPath
	newSandboxArchiveTempPath = func() (string, error) { return "/tmp/.daytona-cp-test.tar", nil }
	defer func() { newSandboxArchiveTempPath = oldTempPath }()
	_, _, err := runToolboxCLI(t, []string{"sandbox", "cp", "-r", localDir, "sbx:/remote"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodPost, path: "/sandboxes/sbx/files/sign", status: 200, auth: true, response: signedFileURLFixture(t, "POST", "/tmp/.daytona-cp-test.tar"), check: expectSignFileRequest("POST", "/tmp/.daytona-cp-test.tar")},
		{method: http.MethodPost, path: "/test-tools/sbx/files/upload", status: 200, auth: false, check: func(t *testing.T, r *http.Request, body []byte) {
			expectSignedTransferQuery("/tmp/.daytona-cp-test.tar")(t, r, body)
			_, params, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
			require.NoError(t, err)
			reader := multipart.NewReader(bytes.NewReader(body), params["boundary"])
			part, partErr := reader.NextPart()
			require.NoError(t, partErr)
			require.Equal(t, ".daytona-cp-test.tar", part.FileName())
			archiveBytes, readErr := io.ReadAll(part)
			require.NoError(t, readErr)
			archive := tar.NewReader(bytes.NewReader(archiveBytes))
			header, nextErr := archive.Next()
			require.NoError(t, nextErr)
			require.Equal(t, "demo.txt", header.Name)
			fileBody, fileErr := io.ReadAll(archive)
			require.NoError(t, fileErr)
			require.Equal(t, "payload", string(fileBody))
		}},
		{method: http.MethodPost, path: "/test-tools/sbx/processes", status: 201, auth: true, query: map[string][]string{"logs": {"text"}, "waitMs": {"1000"}}, response: `{"id":"proc","state":"terminal","kind":"exec","stdin":"closed","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","reason":"exited","exitCode":0,"logs":{"frames":[],"nextCursor":"c1","truncatedHead":false,"eof":true}}`, check: func(t *testing.T, _ *http.Request, body []byte) {
			var payload map[string]any
			require.NoError(t, json.Unmarshal(body, &payload))
			require.Equal(t, []any{"sh", "-c", `mkdir -p "$2" && tar -xf "$1" -C "$2" && rm -f "$1"`, "sh", "/tmp/.daytona-cp-test.tar", "/remote"}, payload["argv"])
			require.Equal(t, "closed", payload["stdin"])
		}},
	})
	require.NoError(t, err)
}

func TestToolboxCP_recursiveUploadIntoTrailingSlashDestinationUsesSourceBasename(t *testing.T) {
	tempDir := t.TempDir()
	localDir := filepath.Join(tempDir, "src")
	require.NoError(t, os.MkdirAll(localDir, 0o755))
	require.NoError(t, os.WriteFile(filepath.Join(localDir, "demo.txt"), []byte("payload"), 0o600))
	oldTempPath := newSandboxArchiveTempPath
	newSandboxArchiveTempPath = func() (string, error) { return "/tmp/.daytona-cp-test.tar", nil }
	defer func() { newSandboxArchiveTempPath = oldTempPath }()
	_, _, err := runToolboxCLI(t, []string{"sandbox", "cp", "-r", localDir, "sbx:/remote/"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodPost, path: "/sandboxes/sbx/files/sign", status: 200, auth: true, response: signedFileURLFixture(t, "POST", "/tmp/.daytona-cp-test.tar"), check: expectSignFileRequest("POST", "/tmp/.daytona-cp-test.tar")},
		{method: http.MethodPost, path: "/test-tools/sbx/files/upload", status: 200, auth: false, response: ``},
		{method: http.MethodPost, path: "/test-tools/sbx/processes", status: 201, auth: true, query: map[string][]string{"logs": {"text"}, "waitMs": {"1000"}}, response: `{"id":"proc","state":"terminal","kind":"exec","stdin":"closed","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","reason":"exited","exitCode":0,"logs":{"frames":[],"nextCursor":"c1","truncatedHead":false,"eof":true}}`, check: func(t *testing.T, _ *http.Request, body []byte) {
			var payload map[string]any
			require.NoError(t, json.Unmarshal(body, &payload))
			require.Equal(t, []any{"sh", "-c", `mkdir -p "$2" && tar -xf "$1" -C "$2" && rm -f "$1"`, "sh", "/tmp/.daytona-cp-test.tar", "/remote/src"}, payload["argv"])
		}},
	})
	require.NoError(t, err)
}

func TestToolboxCP_recursiveUploadRemoteExtractFailureStillRemovesArchive(t *testing.T) {
	tempDir := t.TempDir()
	localDir := filepath.Join(tempDir, "src")
	require.NoError(t, os.MkdirAll(localDir, 0o755))
	require.NoError(t, os.WriteFile(filepath.Join(localDir, "demo.txt"), []byte("payload"), 0o600))
	oldTempPath := newSandboxArchiveTempPath
	newSandboxArchiveTempPath = func() (string, error) { return "/tmp/.daytona-cp-test.tar", nil }
	defer func() { newSandboxArchiveTempPath = oldTempPath }()
	_, _, err := runToolboxCLI(t, []string{"sandbox", "cp", "-r", localDir, "sbx:/remote"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodPost, path: "/sandboxes/sbx/files/sign", status: 200, auth: true, response: signedFileURLFixture(t, "POST", "/tmp/.daytona-cp-test.tar"), check: expectSignFileRequest("POST", "/tmp/.daytona-cp-test.tar")},
		{method: http.MethodPost, path: "/test-tools/sbx/files/upload", status: 200, auth: false, response: ``},
		{method: http.MethodPost, path: "/test-tools/sbx/processes", status: 201, auth: true, query: map[string][]string{"logs": {"text"}, "waitMs": {"1000"}}, response: `{"id":"proc","state":"terminal","kind":"exec","stdin":"closed","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","reason":"exited","exitCode":2,"logs":{"frames":[{"channel":"stderr","cursor":"c1","data":"tar failed","timestamp":1}],"nextCursor":"c1","truncatedHead":false,"eof":true}}`},
		{method: http.MethodPost, path: "/test-tools/sbx/processes", status: 201, auth: true, query: map[string][]string{"logs": {"text"}, "waitMs": {"1000"}}, response: `{"id":"cleanup","state":"terminal","kind":"exec","stdin":"closed","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:01Z","reason":"exited","exitCode":0,"logs":{"frames":[],"nextCursor":"c2","truncatedHead":false,"eof":true}}`, check: func(t *testing.T, _ *http.Request, body []byte) {
			// check runs on the handler goroutine: assert, never require.
			var payload map[string]any
			if assert.NoError(t, json.Unmarshal(body, &payload)) {
				assert.Equal(t, []any{"rm", "-f", "/tmp/.daytona-cp-test.tar"}, payload["argv"])
			}
		}},
	})
	require.Error(t, err)
	require.Contains(t, err.Error(), "tar failed")
	// The remote tar's status passes through with its source.
	require.Equal(t, 2, cerr.ExitCode(err))
	var buf bytes.Buffer
	printErrorJSON(&buf, err)
	require.Contains(t, buf.String(), `"source":"PROCESS"`)
}

// Download: archive output arrives as base64url frames, and
// GNU tar pads the archive to a 10 KiB record boundary AFTER the
// end-of-archive marker. The extractor stops at the marker; the padding must
// still be drained so the producer can finish writing to the pipe.
func TestToolboxCP_recursiveDownloadDrainsArchivePaddingAndDecodesBase64URL(t *testing.T) {
	var archive bytes.Buffer
	tw := tar.NewWriter(&archive)
	// Bytes chosen so the base64url form contains '-' and '_'.
	payload := bytes.Repeat([]byte{0xfb, 0xff, 0xbf}, 700)
	require.NoError(t, tw.WriteHeader(&tar.Header{Name: "./blob.bin", Mode: 0o644, Size: int64(len(payload))}))
	_, _ = tw.Write(payload)
	require.NoError(t, tw.Close())
	padded := append(archive.Bytes(), make([]byte, 10240-archive.Len()%10240)...) // GNU record padding
	frame := base64.RawURLEncoding.EncodeToString(padded)
	require.True(t, strings.ContainsAny(frame, "-_"))

	dest := t.TempDir()
	_, _, err := runToolboxCLI(t, []string{"sandbox", "cp", "-r", "sbx:/remote", dest}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodPost, path: "/test-tools/sbx/processes", status: 201, auth: true, query: map[string][]string{"logs": {"base64"}, "waitMs": {"1000"}}, response: `{"id":"proc","state":"terminal","kind":"exec","stdin":"closed","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","reason":"exited","exitCode":0,"logs":{"frames":[{"cursor":"c1","channel":"stdout","timestamp":1,"data":"` + frame + `"}],"nextCursor":"c1","truncatedHead":false,"eof":true}}`},
	})
	require.NoError(t, err)
	got, err := os.ReadFile(filepath.Join(dest, "remote", "blob.bin"))
	require.NoError(t, err)
	require.Equal(t, payload, got)
}

func TestToolboxCP_recursiveDownloadWithoutDirectoryDestinationUsesExactPath(t *testing.T) {
	var archive bytes.Buffer
	tw := tar.NewWriter(&archive)
	payload := []byte("payload")
	require.NoError(t, tw.WriteHeader(&tar.Header{Name: "blob.txt", Typeflag: tar.TypeReg, Mode: 0o644, Size: int64(len(payload))}))
	_, err := tw.Write(payload)
	require.NoError(t, err)
	require.NoError(t, tw.Close())
	frame := base64.RawURLEncoding.EncodeToString(archive.Bytes())

	dest := filepath.Join(t.TempDir(), "renamed")
	_, _, err = runToolboxCLI(t, []string{"sandbox", "cp", "-r", "sbx:/remote", dest}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodPost, path: "/test-tools/sbx/processes", status: 201, auth: true, query: map[string][]string{"logs": {"base64"}, "waitMs": {"1000"}}, response: `{"id":"proc","state":"terminal","kind":"exec","stdin":"closed","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","reason":"exited","exitCode":0,"logs":{"frames":[{"cursor":"c1","channel":"stdout","timestamp":1,"data":"` + frame + `"}],"nextCursor":"c1","truncatedHead":false,"eof":true}}`},
	})
	require.NoError(t, err)
	got, err := os.ReadFile(filepath.Join(dest, "blob.txt"))
	require.NoError(t, err)
	require.Equal(t, payload, got)
}

func TestToolboxCP_recursiveDownloadExtractsHardLinks(t *testing.T) {
	var archive bytes.Buffer
	tw := tar.NewWriter(&archive)
	payload := []byte("payload")
	require.NoError(t, tw.WriteHeader(&tar.Header{Name: "blob.txt", Typeflag: tar.TypeReg, Mode: 0o644, Size: int64(len(payload))}))
	_, err := tw.Write(payload)
	require.NoError(t, err)
	require.NoError(t, tw.WriteHeader(&tar.Header{Name: "copy.txt", Typeflag: tar.TypeLink, Linkname: "blob.txt"}))
	require.NoError(t, tw.Close())
	frame := base64.RawURLEncoding.EncodeToString(archive.Bytes())

	dest := t.TempDir()
	_, _, err = runToolboxCLI(t, []string{"sandbox", "cp", "-r", "sbx:/remote", dest}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodPost, path: "/test-tools/sbx/processes", status: 201, auth: true, query: map[string][]string{"logs": {"base64"}, "waitMs": {"1000"}}, response: `{"id":"proc","state":"terminal","kind":"exec","stdin":"closed","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","reason":"exited","exitCode":0,"logs":{"frames":[{"cursor":"c1","channel":"stdout","timestamp":1,"data":"` + frame + `"}],"nextCursor":"c1","truncatedHead":false,"eof":true}}`},
	})
	require.NoError(t, err)
	original, err := os.ReadFile(filepath.Join(dest, "remote", "blob.txt"))
	require.NoError(t, err)
	copyBytes, err := os.ReadFile(filepath.Join(dest, "remote", "copy.txt"))
	require.NoError(t, err)
	require.Equal(t, payload, original)
	require.Equal(t, payload, copyBytes)
}
