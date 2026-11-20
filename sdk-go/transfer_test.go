// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"context"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestFileUpload_whenBytesOrStreamOrLocal(t *testing.T) {
	for _, source := range []string{"bytes", "stream", "local", "empty"} {
		t.Run(source, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /files/upload": func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(http.StatusNoContent)
			}})
			fs := newFileTestService(t, fake.Server)
			data := []byte{0, 1, 0xff, '\r', '\n'}
			if source == "empty" {
				data = []byte{}
			}
			local := filepath.Join(t.TempDir(), "data.bin")
			require.NoError(t, os.WriteFile(local, data, 0o600))
			var progress []TransferProgress
			opts := &UploadOptions{Atomic: true, OnProgress: func(p TransferProgress) { progress = append(progress, p) }}
			// When
			var err error
			switch source {
			case "bytes", "empty":
				err = fs.WriteFile(t.Context(), "/dir/a.bin", data, opts)
			case "stream":
				err = fs.Upload(t.Context(), "/dir/a.bin", bytes.NewReader(data), opts)
			case "local":
				err = fs.UploadFile(t.Context(), local, "/dir/a.bin", opts)
			}
			// Then
			require.NoError(t, err)
			request := fake.requests()[0]
			require.Equal(t, "true", request.Query.Get("atomic"))
			require.Equal(t, "/dir/a.bin", request.Query.Get("path"))
			_, params, err := mime.ParseMediaType(request.Header.Get("Content-Type"))
			require.NoError(t, err)
			part, err := multipart.NewReader(bytes.NewReader(request.Body), params["boundary"]).NextPart()
			require.NoError(t, err)
			defer func() { require.NoError(t, part.Close()) }()
			content, err := io.ReadAll(part)
			require.NoError(t, err)
			require.Equal(t, "file", part.FormName())
			require.Equal(t, data, content)
			require.NotEmpty(t, progress)
			require.Equal(t, int64(len(data)), progress[len(progress)-1].Bytes)
		})
	}
}

func TestFileDownload_whenStreamingIgnoresMemoryCap(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/download": func(w http.ResponseWriter, _ *http.Request) {
		if _, err := io.WriteString(w, "payload"); err != nil {
			t.Error(err)
		}
	}})
	fs := newFileTestService(t, fake.Server, WithMaxResponseBytes(1))
	var output bytes.Buffer
	var progress []TransferProgress
	// When
	err := fs.Download(t.Context(), "/a", &output, &DownloadOptions{
		OnProgress: func(p TransferProgress) { progress = append(progress, p) },
	})
	// Then
	require.NoError(t, err)
	require.Equal(t, "payload", output.String())
	require.Equal(t, int64(7), progress[len(progress)-1].Bytes)
	require.Equal(t, int64(7), *progress[len(progress)-1].Total)
}

func TestDownloadFile_whenCancelled_preservesDestination(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/download": func(w http.ResponseWriter, _ *http.Request) {
		if _, err := io.WriteString(w, "new"); err != nil {
			t.Error(err)
		}
	}})
	fs := newFileTestService(t, fake.Server)
	directory := t.TempDir()
	local := filepath.Join(directory, "target")
	require.NoError(t, os.WriteFile(local, []byte("old"), 0o640))
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	// When
	err := fs.DownloadFile(ctx, "/a", local, &DownloadOptions{OnProgress: func(TransferProgress) { cancel() }})
	// Then
	require.ErrorIs(t, err, ErrTransferCancelled)
	require.ErrorIs(t, err, context.Canceled)
	body, err := os.ReadFile(local)
	require.NoError(t, err)
	require.Equal(t, "old", string(body))
	entries, err := os.ReadDir(directory)
	require.NoError(t, err)
	require.Len(t, entries, 1)
}

func TestDownloadFile_whenSuccessful_preservesPermissions(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/download": func(w http.ResponseWriter, _ *http.Request) {
		if _, err := io.WriteString(w, "new"); err != nil {
			t.Error(err)
		}
	}})
	fs := newFileTestService(t, fake.Server)
	local := filepath.Join(t.TempDir(), "target")
	require.NoError(t, os.WriteFile(local, []byte("old"), 0o600))
	require.NoError(t, os.Chmod(local, 0o640))
	// When
	err := fs.DownloadFile(t.Context(), "/a", local, nil)
	// Then
	require.NoError(t, err)
	body, err := os.ReadFile(local)
	require.NoError(t, err)
	require.Equal(t, "new", string(body))
	info, err := os.Stat(local)
	require.NoError(t, err)
	require.Equal(t, os.FileMode(0o640), info.Mode().Perm())
}

func TestTransfer_whenProgressPanics_stillCompletes(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /files/upload": func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}})
	fs := newFileTestService(t, fake.Server)
	// When
	err := fs.Upload(t.Context(), "/a", strings.NewReader("data"), &UploadOptions{
		OnProgress: func(TransferProgress) { panic("observer failed") },
	})
	// Then
	require.NoError(t, err)
}
