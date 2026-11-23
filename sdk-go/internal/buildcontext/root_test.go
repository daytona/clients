// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"

	"go.daytona.com/sdk/v2/internal/dockerignore"
)

func testRoot(t *testing.T, directory string) *os.Root {
	t.Helper()
	root, err := os.OpenRoot(directory)
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, root.Close()) })
	return root
}

func TestSourcesRefuseLinkThroughEscapingParent(t *testing.T) {
	// Given a kept link whose lexical target is inside but whose parent escapes.
	outside := fixtureTree(t, treeFixture{Files: map[string]string{"file": "private"}})
	root := fixtureTree(t, treeFixture{
		Files:    map[string]string{".dockerignore": "hidden\n"},
		Symlinks: map[string]string{"hidden": outside, "link": "hidden/file"},
	})
	source, err := NewSource(root, true, 1)
	require.NoError(t, err)

	// When preparing the declared directory.
	result, err := FromSources(t.Context(), "FROM scratch\n", []Source{source})
	if result != nil {
		t.Cleanup(func() { require.NoError(t, result.Close()) })
	}

	// Then the escaping link is refused even though its parent is ignored.
	var failure *Error
	require.ErrorAs(t, err, &failure)
	require.True(t, failure.Invalid)
}

type descentContext struct {
	context.Context
	swap func()
}

func (c *descentContext) Err() error {
	if c.swap != nil {
		swap := c.swap
		c.swap = nil
		swap()
	}
	return c.Context.Err()
}

func TestWalkRefusesDirectorySwappedBeforeDescent(t *testing.T) {
	// Given a directory swapped after collection, at the descent boundary.
	root := fixtureTree(t, treeFixture{Files: map[string]string{"sub/file": "inside"}})
	outside := fixtureTree(t, treeFixture{Files: map[string]string{"file": "private"}})
	names, err := os.ReadDir(root)
	require.NoError(t, err)
	matcher, err := dockerignore.New(nil)
	require.NoError(t, err)
	ctx := &descentContext{Context: t.Context(), swap: func() {
		require.NoError(t, os.Rename(filepath.Join(root, "sub"), filepath.Join(root, "old")))
		require.NoError(t, os.Symlink(outside, filepath.Join(root, "sub")))
	}}
	walk := directoryWalk{ctx: ctx, root: testRoot(t, root), filter: dockerignore.NewFilter(matcher, nil)}

	// When the collected directory is descended into and archived.
	err = walk.visitChild("sub", names[0], nil)
	var output bytes.Buffer
	if err == nil {
		writer := tarWriter{ctx: t.Context(), output: &output}
		err = writer.writeEntries(walk.entries)
	}

	// Then no bytes from outside the context are accepted.
	var failure *Error
	require.ErrorAs(t, err, &failure)
	require.True(t, failure.Invalid)
	require.NotContains(t, output.String(), "private")
}

func TestCollectRefusesFileThroughEscapingParent(t *testing.T) {
	// Given file metadata collected after a parent has become an escaping link.
	outside := fixtureTree(t, treeFixture{Files: map[string]string{"file": "private"}})
	root := fixtureTree(t, treeFixture{Symlinks: map[string]string{"sub": outside}})

	// When collected and opened, even matching outside inode metadata is unsafe.
	item, err := collectEntry(testRoot(t, root), "sub/file")
	var output bytes.Buffer
	if err == nil {
		writer := tarWriter{ctx: t.Context(), output: &output}
		err = writer.writeEntries([]entry{item})
	}

	// Then it is rejected before any outside content is emitted.
	var failure *Error
	require.ErrorAs(t, err, &failure)
	require.True(t, failure.Invalid)
	require.Empty(t, output.Bytes())
}

func TestEntryReadsAnchoredRootAfterRootPathReplacement(t *testing.T) {
	// Given a collected file whose root path is redirected after opening the root.
	root := fixtureTree(t, treeFixture{Files: map[string]string{"file": "inside"}})
	outside := fixtureTree(t, treeFixture{Files: map[string]string{"file": "private"}})
	item, err := collectEntry(testRoot(t, root), "file")
	require.NoError(t, err)
	require.NoError(t, os.Rename(root, root+"-old"))
	t.Cleanup(func() { require.NoError(t, os.RemoveAll(root+"-old")) })
	require.NoError(t, os.Symlink(outside, root))
	var output bytes.Buffer
	writer := tarWriter{ctx: t.Context(), output: &output}

	// When archived.
	err = writer.writeEntries([]entry{item})

	// Then the open root still names the original directory, never its replacement.
	require.NoError(t, err)
	require.Contains(t, output.String(), "inside")
	require.NotContains(t, output.String(), "private")
}
