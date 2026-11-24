// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestImagesContextPreservesDockerfilePathText(t *testing.T) {
	// Given a valid path whose spelling is not its canonical form.
	directory := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(directory, "Dockerfile"), []byte("FROM alpine\n"), 0o600))
	answer := contextTestDeclaration([]map[string]any{})
	answer["state"] = "ready"
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /build-contexts":  jsonHandler(t, answer),
		"POST /images":          jsonHandler(t, map[string]string{"id": "img-build"}),
		"GET /images/img-build": jsonHandler(t, buildImageFixture("pending")),
	})
	client := newTestClient(t, fake.Server)
	// When
	_, err := client.Images().BuildFromContext(t.Context(), directory, &BuildFromContextOptions{Dockerfile: "./Dockerfile"})
	// Then the upload declaration keeps the caller's spelling.
	require.NoError(t, err)
	require.Contains(t, string(fake.requests()[0].Body), `"dockerfile":"./Dockerfile"`)
}

func TestImagesBuildSucceedsWhenTemporaryArchiveCleanupFails(t *testing.T) {
	for _, source := range []string{"builder", "path"} {
		t.Run(source, func(t *testing.T) {
			// Given a valid build whose temporary archive cannot be removed after submission.
			directory, temporary := t.TempDir(), t.TempDir()
			t.Setenv("TMPDIR", temporary)
			require.NoError(t, os.WriteFile(filepath.Join(directory, "Dockerfile"), []byte("FROM alpine\n"), 0o600))
			answer := contextTestDeclaration([]map[string]any{})
			answer["state"] = "ready"
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /build-contexts": jsonHandler(t, answer),
				"POST /images":         jsonHandler(t, map[string]string{"id": "img-build"}),
				"GET /images/img-build": func(w http.ResponseWriter, _ *http.Request) {
					paths, err := filepath.Glob(filepath.Join(temporary, "daytona-context-*.tar"))
					if !assert.NoError(t, err) || !assert.Len(t, paths, 1) {
						w.WriteHeader(http.StatusInternalServerError)
						return
					}
					assert.NoError(t, os.Remove(paths[0]))
					assert.NoError(t, os.Mkdir(paths[0], 0o700))
					assert.NoError(t, os.WriteFile(filepath.Join(paths[0], "occupied"), nil, 0o600))
					writeJSON(t, w, buildImageFixture("pending"))
				},
			})
			client := newTestClient(t, fake.Server)
			build := func(ctx context.Context) (*Image, error) {
				return client.Images().BuildFromContext(ctx, directory, nil)
			}
			if source == "builder" {
				builder := NewImageBuilder("alpine").AddLocalFile(filepath.Join(directory, "Dockerfile"), "/recipe")
				build = func(ctx context.Context) (*Image, error) {
					return client.Images().Build(ctx, builder, nil)
				}
			}
			// When
			image, err := build(t.Context())
			// Then an accepted build remains successful despite best-effort local cleanup.
			require.NoError(t, err)
			require.Equal(t, "img-build", image.ID)
		})
	}
}
