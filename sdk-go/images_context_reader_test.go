// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestImagesBuildFromContextReaderUploadsExactRange(t *testing.T) {
	// Given archive bytes followed by bytes outside the requested range.
	content := []byte("abcDEFGhi")
	reader := bytes.NewReader(append(bytes.Clone(content), []byte("excluded")...))
	digest := sha256.Sum256(content)
	var mu sync.Mutex
	received := make(map[string]string)
	storage := contextStorageServer(t, func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		assert.NoError(t, err)
		mu.Lock()
		received[r.URL.Path] = string(body)
		mu.Unlock()
		w.WriteHeader(http.StatusNoContent)
	})
	temporary := t.TempDir()
	t.Setenv("TMPDIR", temporary)
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /build-contexts": jsonHandler(t, contextTestDeclaration([]map[string]any{
			contextTestPart(1, 3, storage.URL+"/first"),
			contextTestPart(2, 4, storage.URL+"/second"),
			contextTestPart(3, 2, storage.URL+"/third"),
		})),
		"POST /build-contexts/ctx/complete": jsonHandler(t, map[string]string{"state": "ready"}),
		"POST /images":                      jsonHandler(t, map[string]string{"id": "img-build"}),
		"GET /images/img-build":             jsonHandler(t, buildImageFixture("pending")),
	})
	client := newTestClient(t, fake.Server)
	// When
	image, err := client.Images().BuildFromContextReader(t.Context(), reader, int64(len(content)),
		&BuildFromContextOptions{Dockerfile: "./Dockerfile", Timeout: NoTimeout})
	// Then hashing, multipart offsets and the build refer to the requested bytes.
	require.NoError(t, err)
	require.Equal(t, ImageStatusPending, image.Status)
	mu.Lock()
	assert.Equal(t, map[string]string{"/first": "abc", "/second": "DEFG", "/third": "hi"}, received)
	mu.Unlock()
	var declaration struct {
		Bytes      int64  `json:"bytes"`
		Dockerfile string `json:"dockerfile"`
		SHA256     string `json:"sha256"`
	}
	require.NoError(t, json.Unmarshal(fake.requests()[0].Body, &declaration))
	require.EqualValues(t, len(content), declaration.Bytes)
	require.Equal(t, hex.EncodeToString(digest[:]), declaration.SHA256)
	require.Equal(t, "./Dockerfile", declaration.Dockerfile)
	require.JSONEq(t, `{"context_upload_id":"ctx","region_id":"resolved-region"}`, string(fake.requests()[2].Body))
	files, err := os.ReadDir(temporary)
	require.NoError(t, err)
	require.Empty(t, files, "the build owns cleanup of its temporary snapshot")
}

func TestImagesBuildFromContextReaderRejectsInvalidInput(t *testing.T) {
	for _, test := range []struct {
		name string
		size int64
		opts *BuildFromContextOptions
	}{
		{name: "empty"},
		{name: "negative", size: -1},
		{name: "too large", size: 1<<30 + 1},
		{name: "short reader", size: 4},
		{name: "absolute dockerfile", size: 3, opts: &BuildFromContextOptions{Dockerfile: "/Dockerfile"}},
		{name: "escaping dockerfile", size: 3, opts: &BuildFromContextOptions{Dockerfile: "../Dockerfile"}},
		{name: "blank dockerfile", size: 3, opts: &BuildFromContextOptions{Dockerfile: " "}},
		{name: "invalid timeout", size: 3, opts: &BuildFromContextOptions{Timeout: -time.Second}},
		{name: "named expiry", size: 3, opts: &BuildFromContextOptions{
			BuildImageOptions: BuildImageOptions{Name: "named", IdleExpiry: time.Second},
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Images().BuildFromContextReader(t.Context(), bytes.NewReader([]byte("abc")), test.size, test.opts)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestImagesBuildFromContextReaderRejectsNilReader(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	// When
	_, err := client.Images().BuildFromContextReader(t.Context(), nil, 3, nil)
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	var detail *Error
	require.ErrorAs(t, err, &detail)
	require.Equal(t, CodeBadRequest, detail.Code)
	require.Empty(t, fake.requests())
}

func TestImagesBuildFromContextReaderPreservesCancellation(t *testing.T) {
	// Given a cancelled caller and a valid byte archive.
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	// When
	_, err := client.Images().BuildFromContextReader(ctx, bytes.NewReader([]byte("abc")), 3, nil)
	// Then
	require.ErrorIs(t, err, context.Canceled)
	require.Empty(t, fake.requests())
}

func TestImagesBuildFromContextReaderPreservesReadFailure(t *testing.T) {
	// Given a failing caller-owned reader.
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	failure := errors.New("archive source unavailable")
	reader := contextReaderAtFunc(func([]byte, int64) (int, error) { return 0, failure })
	// When
	_, err := client.Images().BuildFromContextReader(t.Context(), reader, 3, nil)
	// Then
	require.ErrorIs(t, err, failure)
	var sdkError *Error
	require.ErrorAs(t, err, &sdkError)
	require.Empty(t, fake.requests())
}

type contextReaderAtFunc func([]byte, int64) (int, error)

func (f contextReaderAtFunc) ReadAt(p []byte, offset int64) (int, error) { return f(p, offset) }
