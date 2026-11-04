// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestWriteDownloadedFile_overwritesRegularDestination(t *testing.T) {
	t.Parallel()
	dst := filepath.Join(t.TempDir(), "out.txt")
	require.NoError(t, os.WriteFile(dst, []byte("original"), 0o644))

	require.NoError(t, WriteDownloadedFile(strings.NewReader("updated"), dst))

	got, err := os.ReadFile(dst)
	require.NoError(t, err)
	require.Equal(t, "updated", string(got))
}

func TestDownloadFile_overwritesRegularDestination(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("updated"))
	}))
	defer srv.Close()

	dst := filepath.Join(t.TempDir(), "out.txt")
	require.NoError(t, os.WriteFile(dst, []byte("original"), 0o644))

	require.NoError(t, transferClient().DownloadFile(context.Background(), srv.URL, dst))

	got, err := os.ReadFile(dst)
	require.NoError(t, err)
	require.Equal(t, "updated", string(got))
}

func TestWriteDownloadedFile_symlinkPlantedDuringDownloadDoesNotRedirect(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink replacement semantics differ on Windows")
	}

	t.Parallel()
	dir := t.TempDir()
	victim := filepath.Join(dir, "victim.txt")
	require.NoError(t, os.WriteFile(victim, []byte("original"), 0o644))
	dst := filepath.Join(dir, "dest.txt")

	pr, pw := io.Pipe()
	downloadDone := make(chan error, 1)
	go func() {
		downloadDone <- WriteDownloadedFile(pr, dst)
	}()

	require.Eventually(t, func() bool {
		entries, err := os.ReadDir(dir)
		if err != nil {
			return false
		}
		for _, entry := range entries {
			if matched, matchErr := filepath.Match(".dest.txt.tmp-*", entry.Name()); matchErr == nil && matched {
				return true
			}
		}
		return false
	}, time.Second, 10*time.Millisecond)

	require.NoError(t, os.Symlink(victim, dst))
	_, err := pw.Write([]byte("payload"))
	require.NoError(t, err)
	require.NoError(t, pw.Close())
	require.NoError(t, <-downloadDone)

	victimBytes, err := os.ReadFile(victim)
	require.NoError(t, err)
	require.Equal(t, "original", string(victimBytes))

	dstInfo, err := os.Lstat(dst)
	require.NoError(t, err)
	require.False(t, dstInfo.Mode()&os.ModeSymlink != 0)

	dstBytes, err := os.ReadFile(dst)
	require.NoError(t, err)
	require.Equal(t, "payload", string(dstBytes))
}

func TestDownloadFile_symlinkPlantedDuringDownloadDoesNotRedirect(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink replacement semantics differ on Windows")
	}

	t.Parallel()
	releaseBody := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		if flusher, ok := w.(http.Flusher); ok {
			flusher.Flush()
		}
		<-releaseBody
		_, _ = w.Write([]byte("payload"))
	}))
	defer srv.Close()

	dir := t.TempDir()
	victim := filepath.Join(dir, "victim.txt")
	require.NoError(t, os.WriteFile(victim, []byte("original"), 0o644))
	dst := filepath.Join(dir, "dest.txt")

	downloadDone := make(chan error, 1)
	go func() {
		downloadDone <- transferClient().DownloadFile(context.Background(), srv.URL, dst)
	}()

	require.Eventually(t, func() bool {
		entries, err := os.ReadDir(dir)
		if err != nil {
			return false
		}
		for _, entry := range entries {
			if matched, matchErr := filepath.Match(".dest.txt.tmp-*", entry.Name()); matchErr == nil && matched {
				return true
			}
		}
		return false
	}, time.Second, 10*time.Millisecond)

	require.NoError(t, os.Symlink(victim, dst))
	close(releaseBody)
	require.NoError(t, <-downloadDone)

	victimBytes, err := os.ReadFile(victim)
	require.NoError(t, err)
	require.Equal(t, "original", string(victimBytes))

	dstInfo, err := os.Lstat(dst)
	require.NoError(t, err)
	require.False(t, dstInfo.Mode()&os.ModeSymlink != 0)

	dstBytes, err := os.ReadFile(dst)
	require.NoError(t, err)
	require.Equal(t, "payload", string(dstBytes))

	entries, err := os.ReadDir(dir)
	require.NoError(t, err)
	for _, entry := range entries {
		matched, matchErr := filepath.Match(".dest.txt.tmp-*", entry.Name())
		require.NoError(t, matchErr)
		require.False(t, matched)
	}
}
