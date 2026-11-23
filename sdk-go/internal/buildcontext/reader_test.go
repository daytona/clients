// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"os"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestFromReaderSnapshotsBytesAcrossChunks(t *testing.T) {
	// Given archive bytes spanning multiple 64 KiB reads.
	content := bytes.Repeat([]byte("archive"), 20000)
	want := bytes.Clone(content)
	digest := sha256.Sum256(want)
	reader := bytes.NewReader(content)
	result, err := FromReader(t.Context(), io.NewSectionReader(reader, 0, reader.Size()), "Dockerfile")
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, result.Close()) })
	clear(content)
	// When the prepared archive is consumed after the source changes.
	got, err := os.ReadFile(result.Path)
	// Then the digest and upload still describe the original bytes.
	require.NoError(t, err)
	require.Equal(t, want, got)
	require.Equal(t, hex.EncodeToString(digest[:]), result.Digest)
}

func TestFromReaderCancelsBetweenReads(t *testing.T) {
	// Given a source that cancels its caller during the first read.
	temporary := t.TempDir()
	t.Setenv("TMPDIR", temporary)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	reader := cancelArchiveReader{cancel: cancel}
	// When
	result, err := FromReader(ctx, io.NewSectionReader(reader, 0, 128<<10), "Dockerfile")
	// Then no archive escapes the failed preparation.
	require.Nil(t, result)
	require.ErrorIs(t, err, context.Canceled)
	files, err := os.ReadDir(temporary)
	require.NoError(t, err)
	require.Empty(t, files)
}

type cancelArchiveReader struct{ cancel context.CancelFunc }

func (r cancelArchiveReader) ReadAt(data []byte, _ int64) (int, error) {
	r.cancel()
	clear(data)
	return len(data), nil
}
