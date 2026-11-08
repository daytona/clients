// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"archive/tar"
	"bytes"
	"io"
	"os"
	"path/filepath"
	"sort"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/daytona/clients/cli/internal/cerr"
)

func writeContextFiles(t *testing.T, root string, files map[string]string) {
	t.Helper()
	for name, content := range files {
		path := filepath.Join(root, filepath.FromSlash(name))
		require.NoError(t, os.MkdirAll(filepath.Dir(path), 0o755))
		require.NoError(t, os.WriteFile(path, []byte(content), 0o600))
	}
}

func uploadedEntries(t *testing.T, upload imageContextUpload) ([]string, int64) {
	t.Helper()
	reader, err := upload.open()
	require.NoError(t, err)
	defer reader.Close()
	raw, err := io.ReadAll(reader)
	require.NoError(t, err)
	tr := tar.NewReader(bytes.NewReader(raw))
	var names []string
	for {
		header, err := tr.Next()
		if err == io.EOF {
			break
		}
		require.NoError(t, err)
		names = append(names, header.Name)
	}
	sort.Strings(names)
	return names, int64(len(raw))
}

func TestImageBuildContext_honoursDockerignore(t *testing.T) {
	root := t.TempDir()
	writeContextFiles(t, root, map[string]string{
		".dockerignore":          "# local only\n.env\n.git\n*.log\nsecrets\n!secrets/public.pem\nDockerfile\n",
		"Dockerfile":             "FROM scratch\n",
		".env":                   "TOKEN=fixture-not-a-secret\n",
		".git/config":            "[core]\n",
		"app.log":                "noise",
		"src/main.go":            "package main\n",
		"src/debug.log":          "kept: * stays in one segment\n",
		"secrets/private.key":    "fixture",
		"secrets/public.pem":     "fixture",
		"secrets/nested/key.pem": "fixture",
	})

	upload, err := imageContextUploadForPath(root, "Dockerfile")
	require.NoError(t, err)
	names, _ := uploadedEntries(t, upload)

	require.Equal(t, []string{
		".dockerignore", // always sent, like the Dockerfile
		"Dockerfile",
		"secrets/", // written before its re-included child
		"secrets/public.pem",
		"src/",
		"src/debug.log",
		"src/main.go",
	}, names)
}

func TestImageBuildContext_sizeCheckCountsOnlyUploadedEntries(t *testing.T) {
	root := t.TempDir()
	writeContextFiles(t, root, map[string]string{
		".dockerignore": "big/\n",
		"Dockerfile":    "FROM scratch\n",
	})
	big := filepath.Join(root, "big", "blob.bin")
	require.NoError(t, os.MkdirAll(filepath.Dir(big), 0o755))
	f, err := os.Create(big)
	require.NoError(t, err)
	require.NoError(t, f.Truncate(imageContextMaxBytes+1))
	require.NoError(t, f.Close())

	upload, err := imageContextUploadForPath(root, "Dockerfile")
	require.NoError(t, err, "an ignored file does not count against the limit")
	names, size := uploadedEntries(t, upload)
	require.Equal(t, []string{".dockerignore", "Dockerfile"}, names)
	require.Equal(t, upload.size, size, "the size check predicts the uploaded archive exactly")

	require.NoError(t, os.Remove(filepath.Join(root, ".dockerignore")))
	_, err = imageContextUploadForPath(root, "Dockerfile")
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), "without the ignore file the context is too large")
}

func TestImageBuildContext_dockerfileSpecificIgnoreFileWins(t *testing.T) {
	root := t.TempDir()
	writeContextFiles(t, root, map[string]string{
		".dockerignore":                     "a.txt\n",
		"build/app.Dockerfile":              "FROM scratch\n",
		"build/app.Dockerfile.dockerignore": "b.txt\nbuild\n",
		"a.txt":                             "a",
		"b.txt":                             "b",
	})

	upload, err := imageContextUploadForPath(root, "build/app.Dockerfile")
	require.NoError(t, err)
	names, _ := uploadedEntries(t, upload)
	require.Equal(t, []string{
		".dockerignore",
		"a.txt", // the root file does not apply when the Dockerfile has its own
		"build/",
		"build/app.Dockerfile",
		"build/app.Dockerfile.dockerignore",
	}, names)
}

func TestImageBuildContext_wildcardExceptionReachesIntoExcludedDirectories(t *testing.T) {
	root := t.TempDir()
	writeContextFiles(t, root, map[string]string{
		".dockerignore":          "vendor\n!**/LICENSE\n",
		"Dockerfile":             "FROM scratch\n",
		"vendor/lib/code.go":     "package lib\n",
		"vendor/lib/LICENSE":     "license",
		"vendor/other/notes.txt": "x",
	})
	upload, err := imageContextUploadForPath(root, "Dockerfile")
	require.NoError(t, err)
	names, _ := uploadedEntries(t, upload)
	require.Equal(t, []string{".dockerignore", "Dockerfile", "vendor/", "vendor/lib/", "vendor/lib/LICENSE"}, names)
}

func TestImageBuildContext_withoutIgnoreFileUploadsEverything(t *testing.T) {
	root := t.TempDir()
	writeContextFiles(t, root, map[string]string{"Dockerfile": "FROM scratch\n", ".env": "x"})
	upload, err := imageContextUploadForPath(root, "Dockerfile")
	require.NoError(t, err)
	names, _ := uploadedEntries(t, upload)
	require.Equal(t, []string{".env", "Dockerfile"}, names)
}

func TestImageBuildContext_invalidPatternIsAUsageError(t *testing.T) {
	root := t.TempDir()
	writeContextFiles(t, root, map[string]string{"Dockerfile": "FROM scratch\n", ".dockerignore": "[a-\n"})
	_, err := imageContextUploadForPath(root, "Dockerfile")
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.Contains(t, err.Error(), ".dockerignore")
}

// `!config` re-includes the directory, not the secrets the other patterns
// exclude inside it: what `docker build` (BuildKit) sends.
func TestImageBuildContext_exceptionForADirectoryKeepsItsSecretsOut(t *testing.T) {
	root := t.TempDir()
	writeContextFiles(t, root, map[string]string{
		".dockerignore":     "**/*.pem\n**/.env\n!config\n",
		"Dockerfile":        "FROM scratch\n",
		"config/server.pem": "fixture",
		"config/.env":       "fixture",
		"config/app.yaml":   "a: 1\n",
	})
	upload, err := imageContextUploadForPath(root, "Dockerfile")
	require.NoError(t, err)
	names, _ := uploadedEntries(t, upload)
	require.Equal(t, []string{".dockerignore", "Dockerfile", "config/", "config/app.yaml"}, names)
}

// The Dockerfile is uploaded even when its name holds pattern characters and
// an ignore pattern excludes its directory.
func TestImageBuildContext_dockerfileWithPatternCharactersIsAlwaysUploaded(t *testing.T) {
	root := t.TempDir()
	writeContextFiles(t, root, map[string]string{
		".dockerignore":   "d\n",
		"d/Docker[1]file": "FROM scratch\n",
		"d/other":         "x",
	})
	upload, err := imageContextUploadForPath(root, "d/Docker[1]file")
	require.NoError(t, err)
	names, _ := uploadedEntries(t, upload)
	require.Equal(t, []string{".dockerignore", "d/", "d/Docker[1]file"}, names)
}

// A pattern that cannot be matched fails the build only when the walk
// evaluates it, as `docker build` does.
func TestImageBuildContext_unmatchablePatternFailsOnlyWhenEvaluated(t *testing.T) {
	root := t.TempDir()
	writeContextFiles(t, root, map[string]string{".dockerignore": "foo\\q\n", "Dockerfile": "FROM scratch\n"})
	_, err := imageContextUploadForPath(root, "Dockerfile")
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.Contains(t, err.Error(), ".dockerignore")
	require.Contains(t, err.Error(), "invalid pattern")

	root = t.TempDir()
	writeContextFiles(t, root, map[string]string{".dockerignore": "a\n!foo\\q\n", "Dockerfile": "FROM scratch\n", "b": "x"})
	upload, err := imageContextUploadForPath(root, "Dockerfile")
	require.NoError(t, err, "an exception that is never evaluated is harmless")
	names, _ := uploadedEntries(t, upload)
	require.Equal(t, []string{".dockerignore", "Dockerfile", "b"}, names)
}
