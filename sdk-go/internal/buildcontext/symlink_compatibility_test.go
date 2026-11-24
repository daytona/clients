// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"archive/tar"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestDirectoryPreservesInternalCyclicLink(t *testing.T) {
	// Given an internal link cycle: the tar contains the link, not its target.
	root := fixtureTree(t, treeFixture{
		Files:    map[string]string{"Dockerfile": "FROM scratch\n"},
		Symlinks: map[string]string{"link": "link"},
	})

	// When preparing and reading the tar.
	result, err := FromDirectory(t.Context(), root, "")
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, result.Close()) })
	file, err := os.Open(result.Path)
	require.NoError(t, err)
	defer func() { require.NoError(t, file.Close()) }()
	reader := tar.NewReader(file)
	_, err = reader.Next()
	require.NoError(t, err)
	link, err := reader.Next()

	// Then the unchanged link text survives, despite target stat reporting ELOOP.
	require.NoError(t, err)
	require.Equal(t, byte(tar.TypeSymlink), link.Typeflag)
	require.Equal(t, "link", link.Linkname)
}

func TestDirectoryPreservesLinkToUnreadableIgnoredTarget(t *testing.T) {
	// Given a kept internal link into an ignored, unreadable directory.
	root := fixtureTree(t, treeFixture{
		Files: map[string]string{
			"Dockerfile": "FROM scratch\n", ".dockerignore": "private\n", "private/file": "value",
		},
		Symlinks: map[string]string{"link": "private/file"},
	})
	private := filepath.Join(root, "private")
	require.NoError(t, os.Chmod(private, 0))
	t.Cleanup(func() { require.NoError(t, os.Chmod(private, 0o755)) })
	if _, err := os.Stat(filepath.Join(private, "file")); !denied(err) {
		t.Skip("filesystem/user bypasses directory mode bits")
	}

	// When preparing the context.
	result, err := FromDirectory(t.Context(), root, "")
	if result != nil {
		t.Cleanup(func() { require.NoError(t, result.Close()) })
	}

	// Then archiving the link does not require reading its ignored internal target.
	require.NoError(t, err)
	require.Equal(t, []string{".dockerignore", "Dockerfile", "link"}, archiveNames(t, result.Path))
}
