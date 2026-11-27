// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/daytona/clients/cli/internal/cerr"
)

// A download to - writes the file to stdout, and nothing else, even with -o
// json; no file named - is created.
func TestToolboxCP_downloadToStdout(t *testing.T) {
	t.Chdir(t.TempDir())
	stdout, _, err := runToolboxCLI(t, []string{"sandbox", "cp", "sbx:/tmp/demo.txt", "-"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodPost, path: "/sandboxes/sbx/files/sign", status: 200, auth: true, response: signedFileURLFixture(t, "GET", "/tmp/demo.txt"), check: expectSignFileRequest("GET", "/tmp/demo.txt")},
		{method: http.MethodGet, path: "/test-tools/sbx/files/download", status: 200, auth: false, responseContentType: "application/octet-stream", response: `payload`, check: expectSignedTransferQuery("/tmp/demo.txt")},
	})
	require.NoError(t, err)
	require.Equal(t, "payload", stdout)
	_, statErr := os.Stat("-")
	require.ErrorIs(t, statErr, os.ErrNotExist)
}

// An upload from - reads stdin, and the JSON acknowledgement names the
// remote file and the bytes sent.
func TestToolboxCP_uploadFromStdin(t *testing.T) {
	old := cpStdin
	cpStdin = strings.NewReader("from stdin")
	t.Cleanup(func() { cpStdin = old })
	stdout, _, err := runToolboxCLI(t, []string{"sandbox", "cp", "-", "sbx:/tmp/in.txt"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodGet, path: "/test-tools/sbx/files/info", status: 404, auth: true, response: `{"code":"FILE_NOT_FOUND","message":"no such file"}`},
		{method: http.MethodPost, path: "/sandboxes/sbx/files/sign", status: 200, auth: true, response: signedFileURLFixture(t, "POST", "/tmp/in.txt"), check: expectSignFileRequest("POST", "/tmp/in.txt")},
		{method: http.MethodPost, path: "/test-tools/sbx/files/upload", status: 200, auth: false, check: func(t *testing.T, r *http.Request, body []byte) {
			expectSignedTransferQuery("/tmp/in.txt")(t, r, body)
			_, params, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
			assert.NoError(t, err)
			part, err := multipart.NewReader(bytes.NewReader(body), params["boundary"]).NextPart()
			assert.NoError(t, err)
			content, _ := io.ReadAll(part)
			assert.Equal(t, "from stdin", string(content))
		}},
	})
	require.NoError(t, err)
	ack := requireJSONStdout(t, stdout)
	require.Equal(t, map[string]any{"source": "-", "destination": "sbx:/tmp/in.txt", "bytes": float64(10)}, ack)
}

// A finished file copy prints its acknowledgement with -o json (it printed
// nothing before).
func TestToolboxCP_downloadPrintsAnAcknowledgement(t *testing.T) {
	dst := filepath.Join(t.TempDir(), "out.txt")
	stdout, _, err := runToolboxCLI(t, []string{"sandbox", "cp", "sbx:/tmp/demo.txt", dst}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodPost, path: "/sandboxes/sbx/files/sign", status: 200, auth: true, response: signedFileURLFixture(t, "GET", "/tmp/demo.txt")},
		{method: http.MethodGet, path: "/test-tools/sbx/files/download", status: 200, auth: false, responseContentType: "application/octet-stream", response: `payload`},
	})
	require.NoError(t, err)
	require.Equal(t, map[string]any{"source": "sbx:/tmp/demo.txt", "destination": dst, "bytes": float64(7)}, requireJSONStdout(t, stdout))
}

func TestToolboxCP_stdioMisuseIsAUsageErrorBeforeAnyRequest(t *testing.T) {
	for _, args := range [][]string{
		{"sandbox", "cp", "-r", "-", "sbx:/tmp/dir"},
		{"sandbox", "cp", "-r", "sbx:/tmp/dir", "-"},
		{"sandbox", "cp", "-", "sbx:/tmp/"},
	} {
		_, _, err := runToolboxCLI(t, args, nil)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), "%v: %v", args, err)
	}
}

func TestToolboxCP_uploadFromStdinRefusesARemoteDirectory(t *testing.T) {
	old := cpStdin
	cpStdin = strings.NewReader("x")
	t.Cleanup(func() { cpStdin = old })
	_, _, err := runToolboxCLI(t, []string{"sandbox", "cp", "-", "sbx:/tmp"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodGet, path: "/test-tools/sbx/files/info", status: 200, auth: true, response: `{"name":"tmp","path":"/tmp","isDir":true,"size":0,"mode":"drwxr-xr-x","modTime":"2026-09-13T00:00:00Z","modifiedAt":"2026-09-13T00:00:00Z","owner":"root","group":"root","permissions":"0755"}`},
	})
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), "%v", err)
}
