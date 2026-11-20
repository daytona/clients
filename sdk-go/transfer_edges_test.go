// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestFileUpload_whenUnauthorized_replaysOnlyReplayableBodies(t *testing.T) {
	for _, replayable := range []bool{true, false} {
		t.Run(map[bool]string{true: "bytes", false: "reader"}[replayable], func(t *testing.T) {
			// Given
			var attempts atomic.Int32
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes/sbx/access-token": jsonHandler(t, map[string]any{
					"access_token": "fresh", "expires_at": "2040-01-01T00:00:00Z",
					"token_type": "Bearer", "operations": []string{"toolbox"},
				}),
				"POST /files/upload": func(w http.ResponseWriter, _ *http.Request) {
					if attempts.Add(1) == 1 {
						w.WriteHeader(http.StatusUnauthorized)
						return
					}
					w.WriteHeader(http.StatusNoContent)
				},
			})
			client := newTestClient(t, fake.Server)
			sb, err := client.newSandbox(sandboxInit{
				Info: SandboxInfo{ID: "sbx"}, AccessToken: "initial", FromCreate: true,
				Options: GetSandboxOptions{ToolboxURL: fake.Server.URL},
			})
			require.NoError(t, err)
			// When
			if replayable {
				err = sb.FS().WriteFile(t.Context(), "/a", []byte("payload"), nil)
			} else {
				err = sb.FS().Upload(t.Context(), "/a", strings.NewReader("payload"), nil)
			}
			// Then
			if replayable {
				require.NoError(t, err)
				require.Equal(t, int32(2), attempts.Load())
				requests := fake.requests()
				require.Equal(t, requests[0].Body, requests[len(requests)-1].Body)
				require.Equal(t, "Bearer fresh", requests[len(requests)-1].Header.Get("Authorization"))
			} else {
				require.ErrorIs(t, err, ErrAuthentication)
				require.Equal(t, int32(1), attempts.Load())
			}
		})
	}
}

type fileBlockedReader struct {
	closed chan struct{}
	once   sync.Once
}

func (r *fileBlockedReader) Read([]byte) (int, error) { <-r.closed; return 0, io.ErrClosedPipe }
func (r *fileBlockedReader) Close() error             { r.once.Do(func() { close(r.closed) }); return nil }

func TestFileUpload_whenSourceStalls_timeoutClosesReader(t *testing.T) {
	// Given: a cancellable producer that never supplies file bytes.
	server := httptest.NewServer(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		if _, err := io.Copy(io.Discard, r.Body); err != nil {
			return
		}
	}))
	defer server.Close()
	fs := newFileTestService(t, server)
	reader := &fileBlockedReader{closed: make(chan struct{})}
	// When
	err := fs.Upload(t.Context(), "/a", reader, &UploadOptions{Timeout: 30 * time.Millisecond})
	// Then
	require.ErrorIs(t, err, ErrClientTimeout)
	select {
	case <-reader.closed:
	default:
		t.Fatal("upload cancellation did not close the producer")
	}
}

func TestFileUpload_whenSourceShrinks_reportsChangedSize(t *testing.T) {
	// Given
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if _, err := io.Copy(io.Discard, r.Body); err != nil {
			return
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	fs := newFileTestService(t, server)
	path := filepath.Join(t.TempDir(), "source")
	require.NoError(t, os.WriteFile(path, bytes.Repeat([]byte("x"), 1<<20), 0o600))
	var once sync.Once
	mutated := make(chan error, 1)
	// When
	err := fs.UploadFile(t.Context(), path, "/a", &UploadOptions{OnProgress: func(TransferProgress) {
		once.Do(func() { mutated <- os.Truncate(path, 0) })
	}})
	// Then
	require.NoError(t, <-mutated)
	require.ErrorIs(t, err, ErrBadRequest)
}

func TestFileUpload_whenAtomicUnsupported_neverFallsBack(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /files/upload": func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(t, w, map[string]string{"code": "ATOMIC_UPLOAD_UNSUPPORTED", "source": "TOOLBOX", "message": "unsupported"})
	}})
	fs := newFileTestService(t, fake.Server)
	// When
	err := fs.WriteFile(t.Context(), "/a", []byte("data"), &UploadOptions{Atomic: true})
	// Then
	require.ErrorIs(t, err, ErrAtomicUploadUnsupported)
	require.Len(t, fake.requests(), 1)
}

func TestFileUpload_whenAlreadyCancelled_avoidsLocalIO(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{})
	fs := newFileTestService(t, fake.Server)
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	// When
	err := fs.UploadFile(ctx, filepath.Join(t.TempDir(), "missing"), "/a", nil)
	// Then
	require.ErrorIs(t, err, ErrTransferCancelled)
	require.ErrorIs(t, err, context.Canceled)
	require.Empty(t, fake.requests())
}
