// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build unix

package daytona

import (
	"net/http"
	"os"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"go.daytona.com/sdk/v2/internal/buildcontext"
)

func TestContextUpload_whenArchivePathIsReplacedAfterDeclaration(t *testing.T) {
	for _, replacement := range []string{"regular", "symlink", "fifo"} {
		t.Run(replacement, func(t *testing.T) {
			// Given caller-owned bytes that FromPath hashes without copying.
			input := contextTestArchive(t, []byte("abc"))
			archive, err := buildcontext.FromPath(t.Context(), input.Path, input.Dockerfile)
			require.NoError(t, err)
			t.Cleanup(func() { require.NoError(t, archive.Close()) })
			require.Equal(t, input.Path, archive.Path)
			var puts, completes atomic.Int64
			storage := contextStorageServer(t, func(w http.ResponseWriter, r *http.Request) {
				contextDrainRequest(t, r)
				puts.Add(1)
				w.WriteHeader(http.StatusNoContent)
			})
			api := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /build-contexts": func(w http.ResponseWriter, _ *http.Request) {
					// Swap only after upload initialization recorded the original metadata.
					if !assert.NoError(t, contextReplaceArchive(archive.Path, replacement)) {
						w.WriteHeader(http.StatusInternalServerError)
						return
					}
					if replacement == "fifo" {
						// This test checks descriptor type. Keep its FIFO open so a
						// blocking-open regression fails in the bounded test below.
						keeper, openErr := os.OpenFile(archive.Path, os.O_RDWR|syscall.O_NONBLOCK, 0)
						if !assert.NoError(t, openErr) {
							w.WriteHeader(http.StatusInternalServerError)
							return
						}
						t.Cleanup(func() { require.NoError(t, keeper.Close()) })
					}
					writeJSON(t, w, contextTestDeclaration([]map[string]any{
						contextTestPart(1, 3, storage.URL+"/part"),
					}))
				},
				"POST /build-contexts/ctx/complete": func(w http.ResponseWriter, _ *http.Request) {
					completes.Add(1)
					w.WriteHeader(http.StatusNoContent)
				},
			})
			client := newTestClient(t, api.Server)

			// When a regular file, symlink, or FIFO replaces the archive before reopening.
			_, err = (&Images{client: client}).uploadContext(t.Context(), archive, "")

			// Then no replacement bytes are uploaded and no completion is attempted.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Zero(t, puts.Load())
			require.Zero(t, completes.Load())
		})
	}
}

func TestContextUpload_whenFIFOOpenHasNoWriter(t *testing.T) {
	// Given a FIFO in place of a tar file, with no process opening its write end.
	path := t.TempDir() + "/context.tar"
	require.NoError(t, syscall.Mkfifo(path, 0o600))
	opened := make(chan error, 1)

	// When the low-level opener runs, independently of any preceding path checks.
	go func() {
		file, err := openContextArchive(path)
		if err == nil {
			err = file.Close()
		}
		opened <- err
	}()

	// Then open returns immediately, allowing openPart's fstat to refuse the FIFO.
	select {
	case err := <-opened:
		require.NoError(t, err)
	case <-time.After(time.Second):
		// Release a regressed blocking open so a failing test leaves no goroutine.
		rescue, err := os.OpenFile(path, os.O_RDWR|syscall.O_NONBLOCK, 0)
		require.NoError(t, err)
		require.NoError(t, <-opened)
		require.NoError(t, rescue.Close())
		t.Fatal("archive open blocked waiting for a FIFO writer")
	}
}

func TestContextUpload_whenCallerSuppliesInitialSymlink(t *testing.T) {
	// Given a caller-owned tar selected through an initial symlink.
	input := contextTestArchive(t, []byte("abc"))
	link := input.Path + ".link"
	require.NoError(t, os.Symlink(input.Path, link))
	archive, err := buildcontext.FromPath(t.Context(), link, input.Dockerfile)
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, archive.Close()) })
	storage := newFakeServer(t, map[string]http.HandlerFunc{
		"PUT /part": func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) },
	})
	client := newTestClient(t, storage.Server)
	uploader, err := newContextUploader(client, archive)
	require.NoError(t, err)

	// When the first part reopens the initially resolved regular file.
	err = uploader.putPart(t.Context(), contextUploadPart{number: 1, bytes: 3, url: storage.Server.URL + "/part"})

	// Then valid initial symlinks remain supported and carry the original bytes.
	require.NoError(t, err)
	requests := storage.requests()
	require.Len(t, requests, 1)
	require.Equal(t, []byte("abc"), requests[0].Body)
}

func contextReplaceArchive(path, replacement string) error {
	info, err := os.Stat(path)
	if err != nil {
		return err
	}
	saved := path + ".original"
	if err = os.Rename(path, saved); err != nil {
		return err
	}
	switch replacement {
	case "regular":
		if err = os.WriteFile(path, []byte("xyz"), 0o600); err != nil {
			return err
		}
		return os.Chtimes(path, info.ModTime(), info.ModTime())
	case "symlink":
		return os.Symlink(saved, path)
	case "fifo":
		return syscall.Mkfifo(path, 0o600)
	default:
		return badRequest("unknown replacement fixture")
	}
}
