// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestImagesBuildFromContextUsesVerifiedUploadRegion(t *testing.T) {
	// Given a directory and an already verified identical upload in the default region.
	directory := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(directory, "Dockerfile"), []byte("FROM alpine\n"), 0o600))
	const uploadID = "00000000-0000-4000-8000-000000000001"
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /build-contexts": func(w http.ResponseWriter, r *http.Request) {
			var request struct {
				Dockerfile string `json:"dockerfile"`
				Bytes      int64  `json:"bytes"`
				SHA256     string `json:"sha256"`
			}
			if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
				t.Error(err)
				return
			}
			writeJSON(t, w, map[string]any{
				"id": uploadID, "state": "ready", "region_id": "verified-region",
				"dockerfile": request.Dockerfile, "bytes": request.Bytes, "sha256": request.SHA256, "parts": []any{},
			})
		},
		"POST /images":          jsonHandler(t, map[string]string{"id": "img-build"}),
		"GET /images/img-build": jsonHandler(t, buildImageFixture("pending")),
	})
	client := newTestClient(t, fake.Server)

	// When building without explicitly selecting a region.
	image, err := client.Images().BuildFromContext(t.Context(), directory, nil)

	// Then submission names the upload's actual region and does not reupload reused content.
	require.NoError(t, err)
	require.Equal(t, ImageStatusPending, image.Status)
	require.Len(t, fake.requests(), 3)
	require.JSONEq(t, `{"context_upload_id":"`+uploadID+`","region_id":"verified-region"}`, string(fake.requests()[1].Body))
}

func TestImagesContextRefusesOptionsBeforeReadingFiles(t *testing.T) {
	tests := []struct {
		name    string
		options BuildFromContextOptions
	}{
		{"name and expiry", BuildFromContextOptions{BuildImageOptions: BuildImageOptions{Name: "named", IdleExpiry: time.Second}}},
		{"negative timeout", BuildFromContextOptions{Timeout: -time.Second}},
		{"blank dockerfile", BuildFromContextOptions{Dockerfile: "   "}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			// Given a nonexistent directory and invalid options.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When building.
			_, err := client.Images().BuildFromContext(t.Context(), filepath.Join(t.TempDir(), "missing"), &test.options)
			// Then the options error precedes any filesystem or HTTP work.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestImagesBuilderRejectsNamedExpiryBeforeReadingLocalFile(t *testing.T) {
	// Given an unavailable local source and incompatible context naming options.
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	builder := NewImageBuilder("alpine").AddLocalFile(filepath.Join(t.TempDir(), "missing"), "/app")

	// When building.
	_, err := client.Images().Build(t.Context(), builder, &BuildImageOptions{Name: "named", IdleExpiry: time.Second})

	// Then invalid naming cannot cause a billed upload.
	require.ErrorIs(t, err, ErrBadRequest)
	require.Empty(t, fake.requests())
}

func TestImagesContextClassifiesMissingPath(t *testing.T) {
	// Given an unavailable caller-supplied context path.
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	filename := filepath.Join(t.TempDir(), "missing.tar")

	// When preparing a build context.
	_, err := client.Images().BuildFromContext(t.Context(), filename, nil)

	// Then the internal filesystem error crosses the public SDK boundary once.
	require.ErrorIs(t, err, ErrBadRequest)
	require.ErrorIs(t, err, os.ErrNotExist)
	require.Empty(t, fake.requests())
}
