// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"runtime"
	"strings"
	"testing"
	"time"

	apiinternal "github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestSingleFileLocalDestination_treatsTrailingSlashAsDirectoryIntent(t *testing.T) {
	got := SingleFileLocalDestination("/tmp/newdir/", "/remote/demo.txt")
	if got != "/tmp/newdir/demo.txt" {
		t.Fatalf("got %q", got)
	}
}

// Directory intent follows the platform's separators only: on Windows `C:\`
// is the drive root and the download lands at `C:\demo.txt`; on POSIX a
// trailing backslash is part of the file name, so `/tmp/name\` is the
// requested file, not a directory.
func TestSingleFileLocalDestination_volumeRootAndBackslashFollowThePlatform(t *testing.T) {
	if runtime.GOOS == "windows" {
		require.Equal(t, `C:\demo.txt`, SingleFileLocalDestination(`C:\`, "/remote/demo.txt"))
		return
	}
	require.Equal(t, `/tmp/name\`, SingleFileLocalDestination(`/tmp/name\`, "/remote/demo.txt"))
	require.Equal(t, `C:\`, SingleFileLocalDestination(`C:\`, "/remote/demo.txt"))
}

func TestSignFileURL_rejectsInvalidPayloadShapes(t *testing.T) {
	tests := []struct {
		name string
		body string
	}{
		{name: "null", body: `null`},
		{name: "non_object", body: `"nope"`},
		{name: "missing_fields", body: `{}`},
		{name: "missing_url", body: `{"method":"GET","path":"/tmp/demo.txt","expires_at":"2026-06-12T00:42:47Z","signature":"v1_sig"}`},
		{name: "non_string_url", body: `{"url":123,"method":"GET","path":"/tmp/demo.txt","expires_at":"2026-06-12T00:42:47Z","signature":"v1_sig"}`},
		{name: "null_url", body: `{"url":null,"method":"GET","path":"/tmp/demo.txt","expires_at":"2026-06-12T00:42:47Z","signature":"v1_sig"}`},
		{name: "empty_url", body: `{"url":"","method":"GET","path":"/tmp/demo.txt","expires_at":"2026-06-12T00:42:47Z","signature":"v1_sig"}`},
		{name: "relative_url", body: `{"url":"/test-tools/sbx/files/download?path=%2Ftmp%2Fdemo.txt","method":"GET","path":"/tmp/demo.txt","expires_at":"2026-06-12T00:42:47Z","signature":"v1_sig"}`},
		{name: "missing_signature", body: `{"url":"https://tb.example/test-tools/sbx/files/download","method":"GET","path":"/tmp/demo.txt","expires_at":"2026-06-12T00:42:47Z"}`},
		{name: "empty_signature", body: `{"url":"https://tb.example/test-tools/sbx/files/download","method":"GET","path":"/tmp/demo.txt","expires_at":"2026-06-12T00:42:47Z","signature":""}`},
		{name: "missing_expires_at", body: `{"url":"https://tb.example/test-tools/sbx/files/download","method":"GET","path":"/tmp/demo.txt","signature":"v1_sig"}`},
		{name: "invalid_expires_at", body: `{"url":"https://tb.example/test-tools/sbx/files/download","method":"GET","path":"/tmp/demo.txt","expires_at":"soon","signature":"v1_sig"}`},
		{name: "zero_expires_at", body: `{"url":"https://tb.example/test-tools/sbx/files/download","method":"GET","path":"/tmp/demo.txt","expires_at":"0001-01-01T00:00:00Z","signature":"v1_sig"}`},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				assert.Equal(t, http.MethodPost, r.Method)
				assert.Equal(t, "/sandboxes/sbx/files/sign", r.URL.Path)
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write([]byte(tt.body))
			}))
			defer server.Close()
			apiClient := apiinternal.New(server.URL, "k", "test")
			_, err := SignFileURL(context.Background(), apiClient, "linux", "sbx", http.MethodGet, "/tmp/demo.txt", time.Hour)
			require.Error(t, err)
			require.Equal(t, cerr.ExitFailure, cerr.ExitCode(err))
			require.EqualError(t, err, "API returned an invalid signed file URL payload")
		})
	}
}

// The sign request carries exactly the contract's three fields; the response
// URL is used verbatim for the requested sandbox.
func TestSignFileURL_postsTheContractBodyAndReturnsTheURLVerbatim(t *testing.T) {
	now := time.Unix(1781230967, 0)
	oldNow := signedFileNow
	signedFileNow = func() time.Time { return now }
	t.Cleanup(func() { signedFileNow = oldNow })

	cases := []struct {
		name     string
		class    string
		method   string
		wantPath string
	}{
		{name: "linux download", class: "linux", method: http.MethodGet, wantPath: "/sandboxes/sbx/files/sign"},
		{name: "macOS upload", class: "macos", method: http.MethodPost, wantPath: "/sandboxes/sbx/files/sign"},
		{name: "lowercase method is normalized", class: "linux", method: "post", wantPath: "/sandboxes/sbx/files/sign"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var gotBody map[string]any
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				assert.Equal(t, http.MethodPost, r.Method)
				assert.Equal(t, tc.wantPath, r.URL.Path)
				assert.Equal(t, "Bearer k", r.Header.Get("Authorization"))
				body, err := io.ReadAll(r.Body)
				assert.NoError(t, err)
				assert.NoError(t, json.Unmarshal(body, &gotBody))
				w.Header().Set("Content-Type", "application/json")
				_, _ = io.WriteString(w, `{"url":"https://tb.example/test-tools/sbx/files/x?path=%2Ftmp%2Fa%20b.txt&expires=1781234567&signature=v1_sig","method":"GET","path":"/tmp/a b.txt","expires_at":"2026-06-12T00:42:47Z","signature":"v1_sig"}`)
			}))
			defer server.Close()
			apiClient := apiinternal.New(server.URL, "k", "test")
			got, err := SignFileURL(context.Background(), apiClient, tc.class, "sbx", tc.method, "/tmp/a b.txt", time.Hour)
			require.NoError(t, err)
			require.Equal(t, "https://tb.example/test-tools/sbx/files/x?path=%2Ftmp%2Fa%20b.txt&expires=1781234567&signature=v1_sig", got)
			require.Equal(t, map[string]any{
				"method":     strings.ToUpper(tc.method),
				"path":       "/tmp/a b.txt",
				"expires_at": now.Add(time.Hour).Format(time.RFC3339),
			}, gotBody)
		})
	}
}

// Unsupported sandbox classes, methods and URL lifetimes are rejected locally.
func TestSignFileURL_refusesLocallyBeforeAnyRequest(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Errorf("unexpected request %s %s", r.Method, r.URL.String())
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer server.Close()
	apiClient := apiinternal.New(server.URL, "k", "test")

	_, err := SignFileURL(context.Background(), apiClient, "unsupported", "sbx", http.MethodGet, "/tmp/demo.txt", time.Hour)
	require.EqualError(t, err, "signed file transfers are not available for sandbox class \"unsupported\"")
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))

	_, err = SignFileURL(context.Background(), apiClient, "linux", "sbx", http.MethodPut, "/tmp/demo.txt", time.Hour)
	require.EqualError(t, err, `unsupported signing operation "PUT"`)

	for _, ttl := range []time.Duration{0, -time.Second, 500 * time.Millisecond, SignedFileMaxTTL + time.Second} {
		_, err = SignFileURL(context.Background(), apiClient, "linux", "sbx", http.MethodGet, "/tmp/demo.txt", ttl)
		require.Error(t, err, "ttl %s", ttl)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
		require.Contains(t, err.Error(), "signed URL ttl must be between 1s and 168h0m0s (7 days)")
	}
	require.Equal(t, 7*24*time.Hour, SignedFileMaxTTL)
}

// The API's refusals keep the CLI's usual exit codes: 400 (expires outside
// the window) is a usage error, 403 (missing write:sandboxes or an unseated
// member) is forbidden, 404 (other project) is not found.
func TestSignFileURL_mapsAPIErrors(t *testing.T) {
	cases := []struct {
		status   int
		code     string
		wantExit int
	}{
		{status: http.StatusBadRequest, code: "BAD_REQUEST", wantExit: cerr.ExitUsage},
		{status: http.StatusForbidden, code: "FORBIDDEN", wantExit: cerr.ExitForbidden},
		{status: http.StatusNotFound, code: "NOT_FOUND", wantExit: cerr.ExitNotFound},
	}
	for _, tc := range cases {
		t.Run(tc.code, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				assert.Equal(t, "/sandboxes/sbx/files/sign", r.URL.Path)
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(tc.status)
				_, _ = io.WriteString(w, `{"code":"`+tc.code+`","message":"refused","trace_id":"t1"}`)
			}))
			defer server.Close()
			apiClient := apiinternal.New(server.URL, "k", "test")
			_, err := SignFileURL(context.Background(), apiClient, "linux", "sbx", http.MethodGet, "/tmp/demo.txt", time.Hour)
			require.Error(t, err)
			require.Equal(t, tc.wantExit, cerr.ExitCode(err))
			require.Contains(t, err.Error(), "POST /sandboxes/sbx/files/sign")
			require.Contains(t, err.Error(), "refused")
		})
	}
}
