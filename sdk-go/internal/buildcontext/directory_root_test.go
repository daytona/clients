// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestDirectoryRootRefusesSwapAfterStat(t *testing.T) {
	for _, replaced := range []string{"root", "parent"} {
		t.Run(replaced, func(t *testing.T) {
			// Given metadata captured before a source root or its parent is redirected.
			parent := fixtureTree(t, treeFixture{Files: map[string]string{"root/file": "inside"}})
			outside := fixtureTree(t, treeFixture{Files: map[string]string{"root/file": "private"}})
			directory := filepath.Join(parent, "root")
			info, err := os.Stat(directory)
			require.NoError(t, err)
			victim, target := directory, filepath.Join(outside, "root")
			if replaced == "parent" {
				victim, target = parent, outside
			}
			saved := filepath.Join(t.TempDir(), "saved")
			require.NoError(t, os.Rename(victim, saved))
			require.NoError(t, os.Symlink(target, victim))

			// When acquiring the root that will govern traversal and reads.
			root, err := openDirectory(directory, info)
			if root != nil {
				t.Cleanup(func() { require.NoError(t, root.Close()) })
			}

			// Then it cannot silently establish a new baseline outside the selected root.
			var failure *Error
			require.ErrorAs(t, err, &failure)
			require.True(t, failure.Invalid)
		})
	}
}
