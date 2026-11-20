// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	wire "go.daytona.com/sdk/v2/internal/gen/api"
)

func TestContextUpload_whenPartsAreVerified(t *testing.T) {
	// Given an archive split into five storage parts and a caller-owned transport.
	archive := contextTestArchive(t, []byte("abcDEFGhiJKlmn"))
	var mu sync.Mutex
	received := make(map[string][]byte)
	storage := contextStorageServer(t, func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if !assert.NoError(t, err) {
			return
		}
		assert.Equal(t, http.MethodPut, r.Method)
		assert.Equal(t, int64(len(body)), r.ContentLength)
		assert.Empty(t, r.TransferEncoding)
		assert.Empty(t, r.Header.Get("Authorization"))
		assert.Empty(t, r.Header.Get("Cookie"))
		assert.Empty(t, r.Header.Get("X-Daytona-Source"))
		mu.Lock()
		received[r.URL.Path] = body
		mu.Unlock()
		w.WriteHeader(http.StatusNoContent)
	})
	parts := []map[string]any{
		contextTestPart(1, 3, storage.URL+"/part/1?signature=secret"),
		contextTestPart(2, 4, storage.URL+"/part/2"),
		contextTestPart(3, 2, storage.URL+"/part/3"),
		contextTestPart(4, 2, storage.URL+"/part/4"),
		contextTestPart(5, 3, storage.URL+"/part/5"),
	}
	api := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /build-contexts":              jsonHandler(t, contextTestDeclaration(parts)),
		"POST /build-contexts/ctx/complete": jsonHandler(t, map[string]string{"state": "ready"}),
	})
	var storageCalls atomic.Int64
	transport := contextTestTransport(func(r *http.Request) (*http.Response, error) {
		if r.Method == http.MethodPut {
			storageCalls.Add(1)
		}
		return storage.Client().Transport.RoundTrip(r)
	})
	client := newTestClient(t, api.Server, WithHTTPClient(&http.Client{Transport: transport}))

	// When the upload is declared in one region and resolved in another.
	upload, err := (&Images{client: client}).uploadContext(t.Context(), archive, "requested-region")

	// Then the digest, byte layout, authentication boundary and resolved region survive.
	require.NoError(t, err)
	require.Equal(t, buildContextUpload{ID: "ctx", Region: "resolved-region"}, upload)
	require.EqualValues(t, 5, storageCalls.Load())
	mu.Lock()
	assert.Equal(t, map[string][]byte{
		"/part/1": []byte("abc"), "/part/2": []byte("DEFG"), "/part/3": []byte("hi"),
		"/part/4": []byte("JK"), "/part/5": []byte("lmn"),
	}, received)
	mu.Unlock()
	requests := api.requests()
	require.Len(t, requests, 2)
	var declaration wire.CreateBuildContextUploadRequest
	require.NoError(t, json.Unmarshal(requests[0].Body, &declaration))
	require.Equal(t, archive.Size, declaration.Bytes)
	require.Equal(t, archive.Digest, declaration.Sha256)
	require.Equal(t, archive.Dockerfile, *declaration.Dockerfile)
	require.Equal(t, "requested-region", *declaration.RegionId)
	require.NotEmpty(t, requests[0].Header.Get("Authorization"))
	require.JSONEq(t, `{"region_id":"resolved-region"}`, string(requests[1].Body))
	_, statErr := os.Stat(archive.Path)
	require.NoError(t, statErr, "upload borrows the archive; the caller owns its cleanup")
}

func TestContextUpload_whenReadyAtSizeBoundaries(t *testing.T) {
	for _, size := range []int64{1, 64<<20 - 1, 64 << 20, 64<<20 + 1, 1 << 30} {
		t.Run(strconv.FormatInt(size, 10), func(t *testing.T) {
			// Given a prepared archive and an already verified upload.
			archive := contextTestArchive(t, []byte("x"))
			require.NoError(t, os.Truncate(archive.Path, size))
			archive.Size = size
			answer := contextTestDeclaration([]map[string]any{})
			answer["state"] = "ready"
			api := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /build-contexts": jsonHandler(t, answer),
			})
			client := newTestClient(t, api.Server)

			// When any supported archive size is uploaded, including either side of 64 MiB.
			upload, err := (&Images{client: client}).uploadContext(t.Context(), archive, "")

			// Then direct upload is selected and ready reuse sends no storage PUT or completion.
			require.NoError(t, err)
			require.Equal(t, "ctx", upload.ID)
			requests := api.requests()
			require.Len(t, requests, 1)
			var declaration wire.CreateBuildContextUploadRequest
			require.NoError(t, json.Unmarshal(requests[0].Body, &declaration))
			require.Equal(t, size, declaration.Bytes)
			require.Nil(t, declaration.RegionId)
		})
	}
}

func TestContextUpload_whenInputIsInvalid(t *testing.T) {
	for _, name := range []string{"empty", "oversize", "dockerfile", "digest", "missing", "changed"} {
		t.Run(name, func(t *testing.T) {
			// Given invalid prepared archive metadata.
			archive := contextTestArchive(t, []byte("abc"))
			switch name {
			case "empty":
				archive.Size = 0
			case "oversize":
				archive.Size = 1<<30 + 1
			case "dockerfile":
				archive.Dockerfile = " "
			case "digest":
				archive.Digest = strings.Repeat("z", 64)
			case "missing":
				archive.Path += ".missing"
			case "changed":
				archive.Size++
			}
			api := newFakeServer(t, nil)
			client := newTestClient(t, api.Server)

			// When upload is attempted.
			_, err := (&Images{client: client}).uploadContext(t.Context(), archive, "")

			// Then invalid input is refused before any network request.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, api.requests())
		})
	}
}

type contextTestTransport func(*http.Request) (*http.Response, error)

func (f contextTestTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}
