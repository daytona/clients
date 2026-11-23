// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestDownloadFile_whenLocalDirectoryMissing_skipsNetwork(t *testing.T) {
	// Given a valid remote response but no local destination directory.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /files/download": jsonHandler(t, "remote content"),
	})
	fs := newFileTestService(t, fake.Server)
	path := filepath.Join(t.TempDir(), "missing", "target")

	// When preparing a local file download.
	err := fs.DownloadFile(t.Context(), "/remote", path, nil)

	// Then failure is local, before opening any remote stream.
	require.ErrorIs(t, err, os.ErrNotExist)
	require.Empty(t, fake.requests())
}

func TestDownloadFile_whenRemoteMissing_removesStaging(t *testing.T) {
	// Given a writable directory but a missing remote file.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /files/download": http.NotFound,
	})
	fs := newFileTestService(t, fake.Server)
	directory := t.TempDir()

	// When the stream cannot be opened after preparing the destination.
	err := fs.DownloadFile(t.Context(), "/missing", filepath.Join(directory, "target"), nil)

	// Then no staging artifact or destination survives.
	require.ErrorIs(t, err, ErrNotFound)
	entries, readErr := os.ReadDir(directory)
	require.NoError(t, readErr)
	require.Empty(t, entries)
}
