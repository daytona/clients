// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"archive/tar"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	apiinternal "github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

const directUploadID = "22222222-2222-2222-2222-222222222222"

// directUploadPeer fakes the API and the storage endpoint the part URLs name.
type directUploadPeer struct {
	t         *testing.T
	api       *httptest.Server
	storage   *httptest.Server
	ready     bool
	partSize  int64
	putStatus []int // per PUT, in order; 200 once exhausted

	mu        sync.Mutex
	declared  map[string]any
	completed bool
	build     map[string]any
	parts     map[string][]byte
	puts      int
}

func newDirectUploadPeer(t *testing.T, partSize int64) *directUploadPeer {
	p := &directUploadPeer{t: t, partSize: partSize, parts: map[string][]byte{}}
	p.storage = httptest.NewServer(http.HandlerFunc(p.serveStorage))
	p.api = httptest.NewServer(http.HandlerFunc(p.serveAPI))
	t.Cleanup(p.storage.Close)
	t.Cleanup(p.api.Close)
	oldMax, oldBackoff := imageContextMaxBytes, imageContextPartBackoff
	imageContextMaxBytes = 4
	imageContextPartBackoff = func(int) time.Duration { return time.Millisecond }
	t.Cleanup(func() { imageContextMaxBytes, imageContextPartBackoff = oldMax, oldBackoff })
	return p
}

func (p *directUploadPeer) serveStorage(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	p.mu.Lock()
	defer p.mu.Unlock()
	require.Equal(p.t, http.MethodPut, r.Method)
	require.Empty(p.t, r.Header.Get("Authorization"))
	status := http.StatusOK
	if p.puts < len(p.putStatus) {
		status = p.putStatus[p.puts]
	}
	p.puts++
	if status == http.StatusOK {
		p.parts[r.URL.Query().Get("partNumber")] = body
	}
	w.WriteHeader(status)
}

func (p *directUploadPeer) serveAPI(w http.ResponseWriter, r *http.Request) {
	var body map[string]any
	_ = json.NewDecoder(r.Body).Decode(&body)
	require.Equal(p.t, "Bearer k", r.Header.Get("Authorization"))
	p.mu.Lock()
	defer p.mu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	switch {
	case r.Method == http.MethodPost && r.URL.Path == "/build-contexts":
		p.declared = body
		size := int64(body["bytes"].(float64))
		upload := map[string]any{
			"id": directUploadID, "region_id": "eu", "dockerfile": body["dockerfile"],
			"bytes": size, "sha256": body["sha256"], "parts": []any{},
		}
		if p.ready {
			upload["state"] = "ready"
			w.WriteHeader(http.StatusOK)
		} else {
			upload["state"] = "uploading"
			upload["part_size"] = p.partSize
			var parts []any
			for n, offset := 1, int64(0); offset < size; n, offset = n+1, offset+p.partSize {
				parts = append(parts, map[string]any{
					"part_number": n,
					"bytes":       min(p.partSize, size-offset),
					"url":         fmt.Sprintf("%s/bucket/key?partNumber=%d&X-Amz-Signature=secret", p.storage.URL, n),
				})
			}
			upload["parts"] = parts
			w.WriteHeader(http.StatusCreated)
		}
		_ = json.NewEncoder(w).Encode(upload)
	case r.Method == http.MethodPost && r.URL.Path == "/build-contexts/"+directUploadID+"/complete":
		require.Equal(p.t, "eu", body["region_id"])
		p.completed = true
		_ = json.NewEncoder(w).Encode(map[string]any{
			"id": directUploadID, "region_id": "eu", "dockerfile": "Dockerfile",
			"bytes": 0, "sha256": "", "parts": []any{}, "state": "ready",
		})
	case r.Method == http.MethodPost && r.URL.Path == "/images":
		p.build = body
		w.WriteHeader(http.StatusAccepted)
		_, _ = io.WriteString(w, `{"id":"11111111-1111-1111-1111-111111111111","status":"pending"}`)
	default:
		p.t.Fatalf("unexpected API request %s %s", r.Method, r.URL.Path)
	}
}

func (p *directUploadPeer) post(t *testing.T, contextPath string) error {
	t.Helper()
	client := apiinternal.New(p.api.URL, "k", "test")
	resp, err := postImageMultipart(context.Background(), client,
		map[string]string{"name": "img", "cpu_arch": "arm64"}, "build/Dockerfile", contextPath)
	if err == nil {
		require.Equal(t, "11111111-1111-1111-1111-111111111111", resp.V.Id)
	}
	return err
}

func writeContextFile(t *testing.T, data []byte) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "context.tar")
	require.NoError(t, os.WriteFile(path, data, 0o600))
	return path
}

func sha256Hex(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func TestPostImageDirect_uploadsPartsThenBuildsFromTheUpload(t *testing.T) {
	p := newDirectUploadPeer(t, 4)
	data := []byte("0123456789")
	require.NoError(t, p.post(t, writeContextFile(t, data)))

	require.Equal(t, sha256Hex(data), p.declared["sha256"])
	require.Equal(t, float64(len(data)), p.declared["bytes"])
	require.Equal(t, "build/Dockerfile", p.declared["dockerfile"])
	require.Equal(t, map[string][]byte{"1": []byte("0123"), "2": []byte("4567"), "3": []byte("89")}, p.parts)
	require.True(t, p.completed)
	require.Equal(t, map[string]any{
		"context_upload_id": directUploadID, "region_id": "eu", "name": "img", "cpu_arch": "arm64",
	}, p.build)
}

func TestPostImageDirect_reusesAReadyUpload(t *testing.T) {
	p := newDirectUploadPeer(t, 4)
	p.ready = true
	require.NoError(t, p.post(t, writeContextFile(t, []byte("0123456789"))))
	require.Zero(t, p.puts)
	require.False(t, p.completed)
	require.Equal(t, directUploadID, p.build["context_upload_id"])
}

func TestPostImageDirect_archivesADirectory(t *testing.T) {
	p := newDirectUploadPeer(t, 1<<20)
	dir := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(dir, "Dockerfile"), []byte("FROM scratch\n"), 0o600))
	require.NoError(t, p.post(t, dir))

	archive := p.parts["1"]
	require.Equal(t, sha256Hex(archive), p.declared["sha256"])
	require.Equal(t, float64(len(archive)), p.declared["bytes"])
	header, err := tar.NewReader(bytes.NewReader(archive)).Next()
	require.NoError(t, err)
	require.Equal(t, "Dockerfile", header.Name)
}

func TestPostImageDirect_retriesAPartStorageFailed(t *testing.T) {
	p := newDirectUploadPeer(t, 4)
	p.putStatus = []int{http.StatusServiceUnavailable, http.StatusTooManyRequests}
	data := []byte("0123456789")
	require.NoError(t, p.post(t, writeContextFile(t, data)))
	require.Equal(t, 5, p.puts)
	require.Equal(t, []byte("0123"), p.parts["1"])
}

func TestPostImageDirect_refusedPartFailsWithoutTheURL(t *testing.T) {
	p := newDirectUploadPeer(t, 4)
	p.putStatus = []int{http.StatusForbidden}
	err := p.post(t, writeContextFile(t, []byte("0123456789")))
	require.Error(t, err)
	require.Equal(t, cerr.ExitForbidden, cerr.ExitCode(err))
	require.Contains(t, err.Error(), "storage refused build context part 1 (HTTP 403)")
	require.NotContains(t, err.Error(), "Signature")
	require.Equal(t, 1, p.puts)
	require.False(t, p.completed)
	require.Nil(t, p.build)
}

func TestPostImageDirect_givesUpAfterRetries(t *testing.T) {
	p := newDirectUploadPeer(t, 4)
	p.putStatus = []int{http.StatusBadGateway, http.StatusBadGateway, http.StatusBadGateway}
	err := p.post(t, writeContextFile(t, []byte("0123456789")))
	require.Error(t, err)
	require.Contains(t, err.Error(), "storage did not accept build context part 1 (HTTP 502)")
	require.Equal(t, imageContextPartAttempts, p.puts)
}

func TestPostImageDirect_unreachableStorageFailsWithoutTheURL(t *testing.T) {
	p := newDirectUploadPeer(t, 4)
	p.storage.Close()
	err := p.post(t, writeContextFile(t, []byte("0123456789")))
	require.Error(t, err)
	require.Equal(t, "build context part 1 could not reach storage", err.Error())
	require.False(t, strings.Contains(err.Error(), "127.0.0.1"))
}

func TestPostImageDirect_refusesAContextOverTheCap(t *testing.T) {
	p := newDirectUploadPeer(t, 4)
	path := writeContextFile(t, nil)
	require.NoError(t, os.Truncate(path, imageContextDirectMaxBytes+1))
	err := p.post(t, path)
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.Contains(t, err.Error(), "1 GiB")
	require.Nil(t, p.declared)
}
