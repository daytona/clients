// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"

	"go.daytona.com/sdk/v2/internal/dockerignore"
)

func TestWalkRejectsInternalLinkAtDescent(t *testing.T) {
	for _, boundary := range []string{"stale-direntry", "after-collection"} {
		t.Run(boundary, func(t *testing.T) {
			// Given directory metadata followed by an internal symlink replacement.
			root := fixtureTree(t, treeFixture{Files: map[string]string{
				"sub/file": "original", "target/file": "replacement",
			}})
			names, err := os.ReadDir(root)
			require.NoError(t, err)
			require.Equal(t, "sub", names[0].Name())
			matcher, err := dockerignore.New(nil)
			require.NoError(t, err)
			swap := func() {
				require.NoError(t, os.Rename(filepath.Join(root, "sub"), filepath.Join(root, "old")))
				require.NoError(t, os.Symlink("target", filepath.Join(root, "sub")))
			}
			ctx := &descentContext{Context: t.Context()}
			if boundary == "stale-direntry" {
				swap()
			} else {
				ctx.swap = swap
			}
			walk := directoryWalk{ctx: ctx, root: testRoot(t, root), filter: dockerignore.NewFilter(matcher, nil)}

			// When the walker descends using the original DirEntry.
			err = walk.visitChild("sub", names[0], nil)

			// Then an internal link cannot become the parent of archived children.
			var failure *Error
			require.ErrorAs(t, err, &failure)
			require.True(t, failure.Invalid)
		})
	}
}

type walkMutationContext struct {
	context.Context
	remaining int
	swap      func()
}

func (c *walkMutationContext) Err() error {
	c.remaining--
	if c.remaining == 0 {
		c.swap()
	}
	return c.Context.Err()
}

func TestWalkRejectsChangedDeferredParent(t *testing.T) {
	for _, replacement := range []string{"regular-file", "internal-link"} {
		t.Run(replacement, func(t *testing.T) {
			// Given an excluded directory with one kept child and a later ignored entry.
			root := fixtureTree(t, treeFixture{Files: map[string]string{
				"sub/a": "kept", "sub/z": "ignored", "target/a": "replacement",
			}})
			names, err := os.ReadDir(root)
			require.NoError(t, err)
			matcher, err := dockerignore.New([]string{"sub", "!sub/a"})
			require.NoError(t, err)
			ctx := &walkMutationContext{Context: t.Context(), remaining: 3, swap: func() {
				// The third check follows collection of a, before ignored z.
				require.NoError(t, os.Rename(filepath.Join(root, "sub"), filepath.Join(root, "old")))
				if replacement == "regular-file" {
					require.NoError(t, os.WriteFile(filepath.Join(root, "sub"), []byte("data"), 0o600))
				} else {
					require.NoError(t, os.Symlink("target", filepath.Join(root, "sub")))
				}
			}}
			walk := directoryWalk{ctx: ctx, root: testRoot(t, root), filter: dockerignore.NewFilter(matcher, nil)}

			// When the deferred parent is collected after its kept child.
			err = walk.visitChild("sub", names[0], nil)

			// Then the walker refuses a non-directory parent instead of a malformed tar.
			var failure *Error
			require.ErrorAs(t, err, &failure)
			require.True(t, failure.Invalid)
		})
	}
}
