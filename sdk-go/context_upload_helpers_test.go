// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"go.daytona.com/sdk/v2/internal/buildcontext"
)

func contextTestArchive(t *testing.T, data []byte) *buildcontext.Archive {
	t.Helper()
	path := filepath.Join(t.TempDir(), "context.tar")
	require.NoError(t, os.WriteFile(path, data, 0o600))
	digest := sha256.Sum256(data)
	return &buildcontext.Archive{
		Path: path, Size: int64(len(data)), Digest: hex.EncodeToString(digest[:]), Dockerfile: "build/Dockerfile",
	}
}

func contextTestPart(number, size int64, endpoint string) map[string]any {
	return map[string]any{"part_number": number, "bytes": size, "url": endpoint}
}

func contextTestDeclaration(parts []map[string]any) map[string]any {
	return map[string]any{"id": "ctx", "region_id": "resolved-region", "state": "uploading", "parts": parts}
}

func contextTestUploader(t *testing.T, client *Client, data []byte) *contextUploader {
	t.Helper()
	uploader, err := newContextUploader(client, contextTestArchive(t, data))
	require.NoError(t, err)
	uploader.pause = func(ctx context.Context, _ time.Duration) error {
		if ctx.Err() != nil {
			return transportError(ctx, ctx.Err())
		}
		return nil
	}
	return uploader
}

func contextStorageServer(t *testing.T, handler http.HandlerFunc) *httptest.Server {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	return server
}

func contextDrainRequest(t *testing.T, request *http.Request) {
	t.Helper()
	if _, err := io.Copy(io.Discard, request.Body); err != nil {
		t.Errorf("read storage body: %v", err)
	}
}

func contextPartPath(number int64) string { return "/part/" + strconv.FormatInt(number, 10) }
