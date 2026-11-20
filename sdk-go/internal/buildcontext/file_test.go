// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestFromPathPreservesCallerFile(t *testing.T) {
	// Given caller-owned archive bytes, passed through without reserialization.
	content := []byte("caller-owned tar bytes")
	filename := filepath.Join(t.TempDir(), "context.tar")
	require.NoError(t, os.WriteFile(filename, content, 0o600))
	hash := sha256.Sum256(content)
	// When hashed and closed, then byte identity and caller ownership survive.
	result, err := FromPath(t.Context(), filename, "build/Dockerfile")
	require.NoError(t, err)
	require.Equal(t, int64(len(content)), result.Size)
	require.Equal(t, hex.EncodeToString(hash[:]), result.Digest)
	require.Equal(t, "build/Dockerfile", result.Dockerfile)
	require.NoError(t, result.Close())
	require.NoError(t, result.Close())
	got, err := os.ReadFile(filename)
	require.NoError(t, err)
	require.Equal(t, content, got)
}

func TestFromPathRejectsEmptyAndOversize(t *testing.T) {
	for _, size := range []int64{0, MaxBytes + 1} {
		// Given a sparse file outside supported sizes.
		filename := filepath.Join(t.TempDir(), "context.tar")
		require.NoError(t, os.WriteFile(filename, nil, 0o600))
		require.NoError(t, os.Truncate(filename, size))
		// When hashed, then failure is typed before reading the data.
		result, err := FromPath(t.Context(), filename, "")
		require.Nil(t, result)
		var failure *Error
		require.ErrorAs(t, err, &failure)
		require.True(t, failure.Invalid)
	}
}

func TestFromPathPreservesCancellation(t *testing.T) {
	// Given an already cancelled context.
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	// When preparing, then no filesystem work replaces cancellation.
	_, err := FromPath(ctx, filepath.Join(t.TempDir(), "missing"), "")
	require.ErrorIs(t, err, context.Canceled)
}

func TestArchiveCloseRemovesOwnedFile(t *testing.T) {
	// Given an SDK-owned prepared directory archive.
	root := fixtureTree(t, treeFixture{Files: map[string]string{"Dockerfile": "FROM scratch\n"}})
	result, err := FromPath(t.Context(), root, "")
	require.NoError(t, err)
	// When closed, then only the owned temporary archive is removed.
	require.NoError(t, result.Close())
	require.NoError(t, result.Close())
	_, err = os.Stat(result.Path)
	require.ErrorIs(t, err, os.ErrNotExist)
}
