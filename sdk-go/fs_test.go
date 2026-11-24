// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func newFileTestService(t *testing.T, server *httptest.Server, opts ...Option) *FileSystem {
	t.Helper()
	client := newTestClient(t, server, opts...)
	sandbox, err := client.newSandbox(sandboxInit{
		Info:    SandboxInfo{ID: "sbx"},
		Options: GetSandboxOptions{ToolboxURL: server.URL, AccessToken: "fixture"},
	})
	require.NoError(t, err)
	return sandbox.FS()
}

func fileInfoFixture() map[string]any {
	return map[string]any{
		"name": "a.txt", "path": "/root/a.txt", "size": 9, "isDir": false,
		"mode": "-rw-r--r--", "permissions": "0644", "owner": "root", "group": "root",
		"modifiedAt": "2026-01-01T00:00:00Z", "modTime": "2026-01-01",
	}
}

func TestFileMetadata_whenServerReturnsValidInfo(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /files/info": jsonHandler(t, fileInfoFixture()),
	})
	fs := newFileTestService(t, fake.Server)
	// When
	info, err := fs.Stat(t.Context(), "/root/../root/a.txt")
	// Then
	require.NoError(t, err)
	require.Equal(t, int64(9), info.Size)
	require.Equal(t, time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), info.ModifiedAt)
	require.Equal(t, "/root/../root/a.txt", fake.requests()[0].Query.Get("path"))
}

func TestFileList_whenLegacyOrEnvelope(t *testing.T) {
	for _, envelope := range []bool{false, true} {
		t.Run(map[bool]string{false: "legacy", true: "envelope"}[envelope], func(t *testing.T) {
			// Given
			var payload any = []any{fileInfoFixture()}
			if envelope {
				payload = map[string]any{"items": payload, "nextCursor": "next"}
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files": jsonHandler(t, payload)})
			fs := newFileTestService(t, fake.Server)
			// When
			page, err := fs.List(t.Context(), "/root", &ListFilesOptions{Depth: 2})
			// Then
			require.NoError(t, err)
			require.Len(t, page.Items, 1)
			require.Equal(t, "a.txt", page.Items[0].Name)
			require.Equal(t, "2", fake.requests()[0].Query.Get("depth"))
			if envelope {
				require.Equal(t, "next", page.NextCursor)
			}
		})
	}
}

func TestFileMutation_whenOptionsSelected(t *testing.T) {
	cases := []struct {
		name  string
		route string
		query url.Values
		call  func(context.Context, *FileSystem) error
	}{
		{
			"mkdir default", "POST /files/folder",
			url.Values{"path": {"/a"}, "mode": {"0755"}},
			func(ctx context.Context, fs *FileSystem) error { return fs.Mkdir(ctx, "/a", nil) },
		},
		{
			"mkdir mode", "POST /files/folder",
			url.Values{"path": {"/a"}, "mode": {"0700"}},
			func(ctx context.Context, fs *FileSystem) error {
				return fs.Mkdir(ctx, "/a", &MkdirOptions{Mode: "0700"})
			},
		},
		{
			"remove", "DELETE /files",
			url.Values{"path": {"/a"}},
			func(ctx context.Context, fs *FileSystem) error { return fs.Remove(ctx, "/a", nil) },
		},
		{
			"remove tree", "DELETE /files",
			url.Values{"path": {"/a"}, "recursive": {"true"}},
			func(ctx context.Context, fs *FileSystem) error {
				return fs.Remove(ctx, "/a", &RemoveFileOptions{Recursive: true})
			},
		},
		{
			"move", "POST /files/move",
			url.Values{"source": {"/a"}, "destination": {"/b"}},
			func(ctx context.Context, fs *FileSystem) error { return fs.Move(ctx, "/a", "/b") },
		},
		{
			"chmod", "POST /files/permissions",
			url.Values{"path": {"/a"}, "mode": {"0640"}, "owner": {"u"}, "group": {"g"}},
			func(ctx context.Context, fs *FileSystem) error {
				return fs.Chmod(ctx, "/a", &FilePermissionsOptions{Mode: "0640", Owner: "u", Group: "g"})
			},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{tc.route: func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(http.StatusNoContent)
			}})
			fs := newFileTestService(t, fake.Server)
			// When
			err := tc.call(t.Context(), fs)
			// Then
			require.NoError(t, err)
			requests := fake.requests()
			require.Len(t, requests, 1)
			require.Equal(t, tc.query, requests[0].Query)
			require.Empty(t, requests[0].Body)
		})
	}
}

func TestFileMetadata_whenRequiredFieldMalformed(t *testing.T) {
	for _, key := range []string{"name", "path", "size", "isDir", "mode", "permissions", "owner", "group", "modifiedAt", "modTime"} {
		t.Run(key, func(t *testing.T) {
			// Given
			payload := fileInfoFixture()
			delete(payload, key)
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/info": jsonHandler(t, payload)})
			fs := newFileTestService(t, fake.Server)
			// When
			_, err := fs.Stat(t.Context(), "/a")
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}

func TestFileOperation_whenServerError(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/info": func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		writeJSON(t, w, map[string]string{"code": "FILE_NOT_FOUND", "source": "TOOLBOX", "message": "missing"})
	}})
	fs := newFileTestService(t, fake.Server)
	// When
	_, err := fs.Stat(t.Context(), "/a")
	// Then
	require.ErrorIs(t, err, ErrFileNotFound)
	var sdkErr *Error
	require.ErrorAs(t, err, &sdkErr)
	require.Equal(t, "TOOLBOX", sdkErr.Source)
}
