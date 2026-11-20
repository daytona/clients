// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"archive/tar"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

type treeFixture struct {
	Name             string
	Files            map[string]string
	Symlinks         map[string]string
	Fifos            []string
	IgnoreLineLength int
	ContextPath      string
	Dockerfile       string
	Entries          []string
	Error            string
	SHA256           string
}

func fixtureJSON(t *testing.T, name string, destination any) {
	t.Helper()
	content, err := os.ReadFile("../../../sdk-python/tests/fixtures/" + name)
	if errors.Is(err, os.ErrNotExist) {
		t.Skip("shared Python fixtures are not included in the Go module")
	}
	require.NoError(t, err)
	require.NoError(t, json.Unmarshal(content, destination))
}

func fixtureTree(t *testing.T, tc treeFixture) string {
	t.Helper()
	root := t.TempDir()
	for name, contents := range tc.Files {
		filename := filepath.Join(root, name)
		parent := filepath.Dir(filename)
		if strings.HasSuffix(name, "/") {
			parent = filename
		}
		require.NoError(t, os.MkdirAll(parent, 0o755))
		for dir := parent; dir != root; dir = filepath.Dir(dir) {
			require.NoError(t, os.Chmod(dir, 0o755))
		}
		if !strings.HasSuffix(name, "/") {
			require.NoError(t, os.WriteFile(filename, []byte(contents), 0o600))
			require.NoError(t, os.Chmod(filename, 0o644))
		}
	}
	for name, target := range tc.Symlinks {
		filename := filepath.Join(root, name)
		require.NoError(t, os.MkdirAll(filepath.Dir(filename), 0o755))
		require.NoError(t, os.Symlink(target, filename))
	}
	for _, name := range tc.Fifos {
		fixtureFIFO(t, filepath.Join(root, name))
	}
	if tc.IgnoreLineLength > 0 {
		require.NoError(t, os.WriteFile(filepath.Join(root, ".dockerignore"), []byte(strings.Repeat("a", tc.IgnoreLineLength)), 0o600))
	}
	return root
}

func archiveNames(t *testing.T, filename string) []string {
	t.Helper()
	file, err := os.Open(filename)
	require.NoError(t, err)
	defer func() { require.NoError(t, file.Close()) }()
	reader := tar.NewReader(file)
	var names []string
	for {
		header, readErr := reader.Next()
		if readErr == io.EOF {
			break
		}
		require.NoError(t, readErr)
		names = append(names, header.Name)
	}
	return names
}

func TestDirectorySharedArchiveHashes(t *testing.T) {
	var fixtures struct{ Cases []treeFixture }
	fixtureJSON(t, "image-builder-archives.json", &fixtures)
	for _, tc := range fixtures.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			// Given the exact shared tree and normalized permission bits.
			root := fixtureTree(t, tc)
			// When archived, then the complete PAX bytes match both SDKs.
			result, err := FromDirectory(t.Context(), root, "Dockerfile")
			require.NoError(t, err)
			t.Cleanup(func() { require.NoError(t, result.Close()) })
			require.Equal(t, tc.SHA256, result.Digest)
			require.Zero(t, result.Size%10240)
		})
	}
}

func TestDirectorySharedPaths(t *testing.T) {
	var fixtures struct {
		DirectoryCases   []treeFixture
		DirectoryRefused []treeFixture
	}
	fixtureJSON(t, "image-builder-directories.json", &fixtures)
	for _, tc := range append(fixtures.DirectoryCases, fixtures.DirectoryRefused...) {
		t.Run(tc.Name, func(t *testing.T) {
			// Given a shared directory scenario.
			root := fixtureTree(t, tc)
			// When prepared, then selected entries or the typed refusal match.
			result, err := FromDirectory(t.Context(), filepath.Join(root, tc.ContextPath), tc.Dockerfile)
			if tc.Error != "" {
				require.EqualError(t, err, strings.ReplaceAll(tc.Error, "<root>", root))
				var failure *Error
				require.ErrorAs(t, err, &failure)
				require.True(t, failure.Invalid)
				return
			}
			require.NoError(t, err)
			t.Cleanup(func() { require.NoError(t, result.Close()) })
			require.Equal(t, tc.Entries, archiveNames(t, result.Path))
		})
	}
}
