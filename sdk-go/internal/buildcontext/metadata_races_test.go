// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSourceIdentityRejectsMissingDeclarationMetadata(t *testing.T) {
	for _, stage := range []string{"lstat", "eval-symlinks"} {
		t.Run(stage, func(t *testing.T) {
			// Given a source that existed at the initial Stat, then disappears.
			root := fixtureTree(t, treeFixture{
				Files:    map[string]string{"target": "data"},
				Symlinks: map[string]string{"link": "target"},
			})
			local := filepath.Join(root, "target")
			if stage == "eval-symlinks" {
				local = filepath.Join(root, "link")
			}
			_, err := os.Stat(local)
			require.NoError(t, err)
			require.NoError(t, os.Remove(filepath.Join(root, "target")))

			// When the remaining declaration metadata is captured.
			_, _, err = sourceIdentity(local)

			// Then missing local input retains the invalid classification and cause.
			var failure *Error
			require.ErrorAs(t, err, &failure)
			require.True(t, failure.Invalid)
			require.ErrorIs(t, err, os.ErrNotExist)
		})
	}
}

func TestSourceIdentityRejectsUnreadableDeclarationMetadata(t *testing.T) {
	for _, stage := range []string{"lstat", "eval-symlinks"} {
		t.Run(stage, func(t *testing.T) {
			// Given a source whose parent becomes unsearchable after initial Stat.
			root := fixtureTree(t, treeFixture{
				Files:    map[string]string{"hidden/file": "data"},
				Symlinks: map[string]string{"link": "hidden/file"},
			})
			local := filepath.Join(root, "hidden/file")
			if stage == "eval-symlinks" {
				local = filepath.Join(root, "link")
			}
			_, err := os.Stat(local)
			require.NoError(t, err)
			hidden := filepath.Join(root, "hidden")
			require.NoError(t, os.Chmod(hidden, 0))
			t.Cleanup(func() { require.NoError(t, os.Chmod(hidden, 0o755)) })
			if _, err = os.Stat(local); !denied(err) {
				t.Skip("filesystem/user bypasses directory mode bits")
			}

			// When the remaining declaration metadata is captured.
			_, _, err = sourceIdentity(local)

			// Then permission failures are invalid input, with their OS cause.
			var failure *Error
			require.ErrorAs(t, err, &failure)
			require.True(t, failure.Invalid)
			require.ErrorIs(t, err, os.ErrPermission)
		})
	}
}

func TestSymlinkTargetRejectsLinkChangedAfterLstat(t *testing.T) {
	for _, change := range []string{"removed", "regular-file"} {
		t.Run(change, func(t *testing.T) {
			// Given a collected symlink replaced before its target is read.
			root := fixtureTree(t, treeFixture{Symlinks: map[string]string{"link": "target"}})
			local := filepath.Join(root, "link")
			info, err := os.Lstat(local)
			require.NoError(t, err)
			require.NotZero(t, info.Mode()&os.ModeSymlink)
			require.NoError(t, os.Remove(local))
			if change == "regular-file" {
				require.NoError(t, os.WriteFile(local, []byte("data"), 0o600))
			}

			// When collecting the target using the already-open source root.
			_, err = symlinkTarget(testRoot(t, root), "link")

			// Then changed local input is invalid, not an internal error.
			var failure *Error
			require.ErrorAs(t, err, &failure)
			require.True(t, failure.Invalid)
			var cause *os.PathError
			require.ErrorAs(t, err, &cause)
		})
	}
}
