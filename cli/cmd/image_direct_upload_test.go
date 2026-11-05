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
	t        *testing.T
	api      *httptest.Server
	storage  *httptest.Server
	ready    bool
	partSize int64
	// partStatus answers each PUT of a part in turn, keyed by part number;
	// 200 once exhausted.
	partStatus map[string][]int
	// beforePut and beforeComplete run first in each storage PUT and complete
	// call, unlocked, with that call's 0-based index.
	beforePut func(part string, index int)
	// completeStatus answers each complete call in turn; 200 once exhausted.
	completeStatus []int
	beforeComplete func(index int)

	mu        sync.Mutex
	declared  map[string]any
	completed bool
	completes int
	build     map[string]any
	parts     map[string][]byte
	puts      int
	partPuts  map[string]int
}

func newDirectUploadPeer(t *testing.T, partSize int64) *directUploadPeer {
	p := &directUploadPeer{t: t, partSize: partSize, parts: map[string][]byte{}, partPuts: map[string]int{}}
	p.storage = httptest.NewServer(http.HandlerFunc(p.serveStorage))
	p.api = httptest.NewServer(http.HandlerFunc(p.serveAPI))
	t.Cleanup(p.storage.Close)
	t.Cleanup(p.api.Close)
	oldMax, oldBackoff, oldCompleteBackoff := imageContextMaxBytes, imageContextPartBackoff, imageContextCompleteBackoff
	imageContextMaxBytes = 4
	imageContextPartBackoff = func(int) time.Duration { return time.Millisecond }
	imageContextCompleteBackoff = func(int) time.Duration { return time.Millisecond }
	t.Cleanup(func() {
		imageContextMaxBytes, imageContextPartBackoff, imageContextCompleteBackoff = oldMax, oldBackoff, oldCompleteBackoff
	})
	return p
}

func (p *directUploadPeer) serveStorage(w http.ResponseWriter, r *http.Request) {
	part := r.URL.Query().Get("partNumber")
	p.mu.Lock()
	index := p.partPuts[part]
	p.partPuts[part]++
	p.puts++
	p.mu.Unlock()
	if p.beforePut != nil {
		p.beforePut(part, index)
	}
	body, err := io.ReadAll(r.Body)
	if err != nil {
		return
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	require.Equal(p.t, http.MethodPut, r.Method)
	require.Empty(p.t, r.Header.Get("Authorization"))
	status := http.StatusOK
	if statuses := p.partStatus[part]; index < len(statuses) {
		status = statuses[index]
	}
	if status == http.StatusOK {
		p.parts[part] = body
	}
	w.WriteHeader(status)
}

func (p *directUploadPeer) serveAPI(w http.ResponseWriter, r *http.Request) {
	var body map[string]any
	_ = json.NewDecoder(r.Body).Decode(&body)
	require.Equal(p.t, "Bearer k", r.Header.Get("Authorization"))
	completeIndex := -1
	if r.URL.Path == "/build-contexts/"+directUploadID+"/complete" {
		p.mu.Lock()
		completeIndex = p.completes
		p.completes++
		p.mu.Unlock()
		if p.beforeComplete != nil {
			p.beforeComplete(completeIndex)
		}
	}
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
		if completeIndex < len(p.completeStatus) && p.completeStatus[completeIndex] != http.StatusOK {
			w.WriteHeader(p.completeStatus[completeIndex])
			_, _ = io.WriteString(w, `{"code":"INTERNAL_ERROR","message":"try again"}`)
			return
		}
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
	return p.postWith(t, apiinternal.New(p.api.URL, "k", "test"), contextPath)
}

func (p *directUploadPeer) postWith(t *testing.T, client *apiinternal.Client, contextPath string) error {
	t.Helper()
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
	p.partStatus = map[string][]int{"1": {http.StatusServiceUnavailable, http.StatusTooManyRequests}}
	data := []byte("0123456789")
	require.NoError(t, p.post(t, writeContextFile(t, data)))
	require.Equal(t, 5, p.puts)
	require.Equal(t, 3, p.partPuts["1"])
	require.Equal(t, []byte("0123"), p.parts["1"])
}

func TestPostImageDirect_refusedPartFailsWithoutTheURL(t *testing.T) {
	p := newDirectUploadPeer(t, 4)
	p.partStatus = map[string][]int{"1": {http.StatusForbidden}}
	err := p.post(t, writeContextFile(t, []byte("0123456789")))
	require.Error(t, err)
	require.Equal(t, cerr.ExitForbidden, cerr.ExitCode(err))
	require.Contains(t, err.Error(), "storage refused build context part 1 (HTTP 403)")
	require.NotContains(t, err.Error(), "Signature")
	require.Equal(t, 1, p.partPuts["1"])
	require.False(t, p.completed)
	require.Nil(t, p.build)
}

func TestPostImageDirect_givesUpAfterRetries(t *testing.T) {
	p := newDirectUploadPeer(t, 4)
	p.partStatus = map[string][]int{"1": {http.StatusBadGateway, http.StatusBadGateway, http.StatusBadGateway}}
	err := p.post(t, writeContextFile(t, []byte("0123456789")))
	require.Error(t, err)
	require.Contains(t, err.Error(), "storage did not accept build context part 1 (HTTP 502)")
	require.Equal(t, imageContextPartAttempts, p.partPuts["1"])
	require.False(t, p.completed)
}

func TestPostImageDirect_unreachableStorageFailsWithoutTheURL(t *testing.T) {
	p := newDirectUploadPeer(t, 4)
	p.storage.Close()
	err := p.post(t, writeContextFile(t, []byte("0123456789")))
	require.Error(t, err)
	require.Regexp(t, `^build context part [123] could not reach storage$`, err.Error())
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

func TestPostImageDirect_uploadsPartsInParallelUpToTheLimit(t *testing.T) {
	p := newDirectUploadPeer(t, 1)
	var mu sync.Mutex
	inFlight, peak := 0, 0
	full := make(chan struct{})
	p.beforePut = func(string, int) {
		mu.Lock()
		inFlight++
		peak = max(peak, inFlight)
		if inFlight == imageContextPartConcurrency {
			select {
			case <-full:
			default:
				close(full)
			}
		}
		mu.Unlock()
		select {
		case <-full:
		case <-time.After(5 * time.Second):
		}
		mu.Lock()
		inFlight--
		mu.Unlock()
	}
	data := []byte("0123456789")
	require.NoError(t, p.post(t, writeContextFile(t, data)))

	require.Equal(t, imageContextPartConcurrency, peak)
	require.Len(t, p.parts, len(data))
	for i, b := range data {
		require.Equal(t, []byte{b}, p.parts[fmt.Sprint(i+1)])
	}
	require.True(t, p.completed)
}

func TestPostImageDirect_retriesAPartThatTimedOut(t *testing.T) {
	p := newDirectUploadPeer(t, 4)
	old := imageContextStorage
	short := *imageContextStorage
	short.Timeout = 100 * time.Millisecond
	imageContextStorage = &short
	t.Cleanup(func() { imageContextStorage = old })
	p.beforePut = func(part string, index int) {
		if part == "1" && index == 0 {
			time.Sleep(500 * time.Millisecond)
		}
	}
	require.NoError(t, p.post(t, writeContextFile(t, []byte("0123456789"))))
	require.Equal(t, 2, p.partPuts["1"])
	require.Equal(t, []byte("0123"), p.parts["1"])
	require.True(t, p.completed)
}

func TestPostImageDirect_completeOutlivesTheClientTimeout(t *testing.T) {
	p := newDirectUploadPeer(t, 4)
	old := imageContextCompleteTimeout
	imageContextCompleteTimeout = 5 * time.Second
	t.Cleanup(func() { imageContextCompleteTimeout = old })
	p.beforeComplete = func(int) { time.Sleep(300 * time.Millisecond) }
	client := apiinternal.New(p.api.URL, "k", "test")
	client.HTTP.Timeout = 100 * time.Millisecond
	require.NoError(t, p.postWith(t, client, writeContextFile(t, []byte("0123456789"))))
	require.Equal(t, 1, p.completes)
	require.Equal(t, 100*time.Millisecond, client.HTTP.Timeout)
}

func TestPostImageDirect_retriesACompleteThatTimedOut(t *testing.T) {
	p := newDirectUploadPeer(t, 4)
	old := imageContextCompleteTimeout
	imageContextCompleteTimeout = 200 * time.Millisecond
	t.Cleanup(func() { imageContextCompleteTimeout = old })
	p.beforeComplete = func(index int) {
		if index == 0 {
			time.Sleep(700 * time.Millisecond)
		}
	}
	client := apiinternal.New(p.api.URL, "k", "test")
	client.HTTP.Timeout = 50 * time.Millisecond
	require.NoError(t, p.postWith(t, client, writeContextFile(t, []byte("0123456789"))))
	require.True(t, p.completed)
	require.Equal(t, directUploadID, p.build["context_upload_id"])
}

func TestPostImageDirect_retriesACompleteTheServerFailed(t *testing.T) {
	p := newDirectUploadPeer(t, 4)
	p.completeStatus = []int{http.StatusServiceUnavailable, http.StatusBadGateway}
	require.NoError(t, p.post(t, writeContextFile(t, []byte("0123456789"))))
	require.Equal(t, 3, p.completes)
	require.Equal(t, directUploadID, p.build["context_upload_id"])
}

func TestPostImageDirect_givesUpOnCompleteAfterRetries(t *testing.T) {
	p := newDirectUploadPeer(t, 4)
	p.completeStatus = []int{http.StatusInternalServerError, http.StatusInternalServerError, http.StatusInternalServerError}
	err := p.post(t, writeContextFile(t, []byte("0123456789")))
	require.Error(t, err)
	require.Equal(t, imageContextCompleteAttempts, p.completes)
	require.Nil(t, p.build)
}

func TestPostImageDirect_doesNotRetryACompleteTheServerRefused(t *testing.T) {
	p := newDirectUploadPeer(t, 4)
	p.completeStatus = []int{http.StatusConflict}
	err := p.post(t, writeContextFile(t, []byte("0123456789")))
	require.Error(t, err)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
	require.Equal(t, 1, p.completes)
	require.Nil(t, p.build)
}

func TestPostImageDirect_debugOutputNeverNamesAPartURL(t *testing.T) {
	p := newDirectUploadPeer(t, 4)
	var lines []string
	client := apiinternal.New(p.api.URL, "k", "test")
	client.Debug = func(format string, a ...any) { lines = append(lines, fmt.Sprintf(format, a...)) }
	require.NoError(t, p.postWith(t, client, writeContextFile(t, []byte("0123456789"))))
	require.NotEmpty(t, lines)
	for _, line := range lines {
		require.NotContains(t, line, "Signature")
		require.NotContains(t, line, p.storage.URL)
	}
}
