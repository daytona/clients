// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package dockerignore_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/daytona/clients/cli/internal/dockerignore"
	"github.com/daytona/clients/cli/internal/toolbox"
)

// testdata/buildkit_cases.json holds ignore files, context trees and the
// entries `docker build` (BuildKit's context walker, fsutil.Walk with the
// ignore file's patterns) uploads for them: hand-picked cases, including
// secrets inside a re-included directory, and random ones. The CLI's walk
// must upload exactly the same entries.
type buildKitCase struct {
	Ignore   string   `json:"ignore"`
	Files    []string `json:"files"`
	Uploaded []string `json:"uploaded"`
}

func TestWalkUploadsWhatBuildKitUploads(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("testdata", "buildkit_cases.json"))
	require.NoError(t, err)
	var cases []buildKitCase
	require.NoError(t, json.Unmarshal(raw, &cases))
	require.GreaterOrEqual(t, len(cases), 300)
	for i, c := range cases {
		root := t.TempDir()
		for _, f := range c.Files {
			p := filepath.Join(root, filepath.FromSlash(f))
			if strings.HasSuffix(f, "/") {
				require.NoError(t, os.MkdirAll(p, 0o755))
				continue
			}
			require.NoError(t, os.MkdirAll(filepath.Dir(p), 0o755))
			require.NoError(t, os.WriteFile(p, []byte("x"), 0o600))
		}
		patterns, err := dockerignore.ReadPatterns(strings.NewReader(c.Ignore))
		require.NoError(t, err, i)
		m, err := dockerignore.New(patterns)
		require.NoError(t, err, i)
		uploaded := []string{}
		err = toolbox.WalkArchiveEntries(root, dockerignore.NewFilter(m), func(rel, _ string, info os.FileInfo) error {
			if info.IsDir() {
				rel += "/"
			}
			uploaded = append(uploaded, rel)
			return nil
		})
		require.NoError(t, err, i)
		sort.Strings(uploaded)
		require.Equal(t, c.Uploaded, uploaded, "case %d: ignore %q, files %q", i, c.Ignore, c.Files)
	}
}

// Paths given to NewFilter are kept without matching them, so a name with
// pattern characters is kept too, along with its excluded parents.
func TestFilterAlwaysKeepsTheGivenPaths(t *testing.T) {
	root := t.TempDir()
	for _, f := range []string{"d/Docker[1]file", "d/other", ".dockerignore"} {
		p := filepath.Join(root, filepath.FromSlash(f))
		require.NoError(t, os.MkdirAll(filepath.Dir(p), 0o755))
		require.NoError(t, os.WriteFile(p, []byte("x"), 0o600))
	}
	m, err := dockerignore.New([]string{"d", ".dockerignore"})
	require.NoError(t, err)
	var uploaded []string
	require.NoError(t, toolbox.WalkArchiveEntries(root, dockerignore.NewFilter(m, ".dockerignore", "d/Docker[1]file"), func(rel, _ string, _ os.FileInfo) error {
		uploaded = append(uploaded, rel)
		return nil
	}))
	require.Equal(t, []string{".dockerignore", "d", "d/Docker[1]file"}, uploaded)
}

// An excluded directory no exception can reach is never read, and a
// permission error on an excluded directory is ignored, as BuildKit does.
func TestWalkSkipsUnreadableExcludedDirectories(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root reads a mode-000 directory")
	}
	for _, ignore := range []string{"data\n", "data\n!**/keep\n", "data\n!data/keep\n"} {
		root := t.TempDir()
		require.NoError(t, os.WriteFile(filepath.Join(root, "Dockerfile"), []byte("x"), 0o600))
		data := filepath.Join(root, "data")
		require.NoError(t, os.Mkdir(data, 0o755))
		require.NoError(t, os.WriteFile(filepath.Join(data, "x"), []byte("x"), 0o600))
		require.NoError(t, os.Chmod(data, 0))
		t.Cleanup(func() { _ = os.Chmod(data, 0o755) })
		patterns, err := dockerignore.ReadPatterns(strings.NewReader(ignore))
		require.NoError(t, err)
		m, err := dockerignore.New(patterns)
		require.NoError(t, err)
		var uploaded []string
		require.NoError(t, toolbox.WalkArchiveEntries(root, dockerignore.NewFilter(m), func(rel, _ string, _ os.FileInfo) error {
			uploaded = append(uploaded, rel)
			return nil
		}), ignore)
		require.Equal(t, []string{"Dockerfile"}, uploaded, ignore)
	}
	// An unreadable directory that is uploaded is still an error.
	root := t.TempDir()
	data := filepath.Join(root, "data")
	require.NoError(t, os.Mkdir(data, 0o755))
	require.NoError(t, os.Chmod(data, 0))
	t.Cleanup(func() { _ = os.Chmod(data, 0o755) })
	m, err := dockerignore.New([]string{"other"})
	require.NoError(t, err)
	err = toolbox.WalkArchiveEntries(root, dockerignore.NewFilter(m), func(string, string, os.FileInfo) error { return nil })
	require.ErrorIs(t, err, os.ErrPermission)
}
