// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSourceRejectsRedirectedDeclaration(t *testing.T) {
	for _, change := range []string{"file-link", "parent-link", "loop", "file-to-dir", "dir-to-file", "removed"} {
		t.Run(change, func(t *testing.T) {
			// Given a declared path captured before its type or ancestry changes.
			root := fixtureTree(t, treeFixture{Files: map[string]string{
				"source/sub/file": "inside", "outside/file": "secret",
			}})
			local := filepath.Join(root, "source/sub/file")
			isDir := change == "dir-to-file"
			if isDir {
				local = filepath.Dir(local)
			}
			source, err := NewSource(local, isDir, 1)
			require.NoError(t, err)
			if change == "parent-link" {
				require.NoError(t, os.Rename(filepath.Dir(local), filepath.Join(root, "old")))
				require.NoError(t, os.Symlink(filepath.Join(root, "outside"), filepath.Dir(local)))
			} else {
				require.NoError(t, os.Rename(local, filepath.Join(root, "old")))
				switch change {
				case "file-link":
					require.NoError(t, os.Symlink(filepath.Join(root, "outside/file"), local))
				case "loop":
					require.NoError(t, os.Symlink(filepath.Base(local), local))
				case "file-to-dir":
					require.NoError(t, os.Mkdir(local, 0o755))
				case "dir-to-file":
					require.NoError(t, os.WriteFile(local, []byte("secret"), 0o600))
				case "removed":
				}
			}
			// When prepared, then no changed source can become a context.
			result, err := FromSources(t.Context(), "FROM scratch\n", []Source{source})
			require.Nil(t, result)
			var failure *Error
			require.ErrorAs(t, err, &failure)
			require.True(t, failure.Invalid)
		})
	}
}

func TestSourceDeclaredLinkFollowsCurrentTarget(t *testing.T) {
	// Given a declared symlink subsequently pointing at another regular file.
	root := fixtureTree(t, treeFixture{
		Files:    map[string]string{"first": "one", "second": "two"},
		Symlinks: map[string]string{"link": "first"},
	})
	local := filepath.Join(root, "link")
	source, err := NewSource(local, false, 1)
	require.NoError(t, err)
	require.NoError(t, os.Remove(local))
	require.NoError(t, os.Symlink("second", local))
	// When prepared, then the named link is followed to its current target.
	entries, err := source.entries(t.Context())
	require.NoError(t, err)
	require.Equal(t, filepath.Join(root, "second"), entries[1].local)
	require.Equal(t, fileEntry, entries[1].kind)
}

func TestSourceHomeExpansion(t *testing.T) {
	// Given a current home directory with a regular file.
	root := fixtureTree(t, treeFixture{Files: map[string]string{"file": "value"}})
	t.Setenv("HOME", root)
	t.Setenv("USERPROFILE", root)
	// When declared with '~/', then the absolute source stays unresolved.
	source, err := NewSource("~/file", false, 1)
	require.NoError(t, err)
	require.Equal(t, filepath.Join(root, "file"), source.LocalPath)
}

func TestDirectoryRejectsNonUTF8EvenWhenIgnored(t *testing.T) {
	for _, ignored := range []bool{false, true} {
		// Given a filesystem entry that cannot be serialized as Unicode.
		root := fixtureTree(t, treeFixture{Files: map[string]string{"Dockerfile": "FROM scratch\n"}})
		name := filepath.Join(root, "bad\xff")
		if err := os.WriteFile(name, []byte("x"), 0o600); err != nil {
			t.Skip("filesystem cannot represent an invalid UTF-8 name")
		}
		if ignored {
			require.NoError(t, os.WriteFile(filepath.Join(root, ".dockerignore"), []byte("bad*\n"), 0o600))
		}
		// When walked, then ignore rules cannot conceal an invalid name.
		_, err := FromDirectory(t.Context(), root, "")
		var failure *Error
		require.ErrorAs(t, err, &failure)
		require.True(t, failure.Invalid)
	}
}

func TestDirectoryRejectsInvalidLinkTarget(t *testing.T) {
	// Given a link with an invalid UTF-8 target.
	root := fixtureTree(t, treeFixture{Files: map[string]string{"Dockerfile": "FROM scratch\n"}})
	if err := os.Symlink("bad\xff", filepath.Join(root, "link")); err != nil {
		t.Skip("filesystem cannot represent an invalid UTF-8 target")
	}
	// When walked, then it cannot be serialized with replacement bytes.
	_, err := FromDirectory(t.Context(), root, "")
	var failure *Error
	require.ErrorAs(t, err, &failure)
	require.True(t, failure.Invalid)
}
