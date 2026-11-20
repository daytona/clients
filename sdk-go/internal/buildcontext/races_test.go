// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"archive/tar"
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestArchiveRejectsReplacedEntry(t *testing.T) {
	for _, change := range []string{"file-link", "parent-link", "replacement", "fifo", "removed"} {
		t.Run(change, func(t *testing.T) {
			// Given an identity recorded before another tree replaces the path.
			root := fixtureTree(t, treeFixture{Files: map[string]string{
				"source/sub/file": "inside", "outside/file": "secret",
			}})
			victim := filepath.Join(root, "source/sub/file")
			item, err := collectEntry(victim, "file")
			require.NoError(t, err)
			switch change {
			case "parent-link":
				require.NoError(t, os.Rename(filepath.Dir(victim), filepath.Join(root, "old")))
				require.NoError(t, os.Symlink(filepath.Join(root, "outside"), filepath.Dir(victim)))
			default:
				require.NoError(t, os.Rename(victim, filepath.Join(root, "old")))
				switch change {
				case "file-link":
					require.NoError(t, os.Symlink(filepath.Join(root, "outside/file"), victim))
				case "replacement":
					require.NoError(t, os.WriteFile(victim, []byte("secret"), 0o600))
				case "fifo":
					fixtureFIFO(t, victim)
				case "removed":
				}
			}
			// When opened for archiving, then no replacement bytes are copied.
			var output bytes.Buffer
			writer := tarWriter{ctx: t.Context(), output: &output}
			err = writer.writeEntries([]entry{item})
			var failure *Error
			require.ErrorAs(t, err, &failure)
			require.True(t, failure.Invalid)
			require.Empty(t, output.Bytes())
		})
	}
}

type hookedWriter struct {
	output io.Writer
	hook   func()
}

func (w *hookedWriter) Write(data []byte) (int, error) {
	if w.hook != nil {
		hook := w.hook
		w.hook = nil
		hook()
	}
	return w.output.Write(data)
}

func TestArchiveUsesOpenedSize(t *testing.T) {
	for _, change := range []string{"grow-before-open", "grow-after-open", "shrink-after-open"} {
		t.Run(change, func(t *testing.T) {
			// Given a collected file whose contents change at a fixed I/O boundary.
			root := fixtureTree(t, treeFixture{Files: map[string]string{"file": "original"}})
			filename := filepath.Join(root, "file")
			item, err := collectEntry(filename, "file")
			require.NoError(t, err)
			mutate := func() {
				value := "original appended"
				if change == "shrink-after-open" {
					value = ""
				}
				require.NoError(t, os.WriteFile(filename, []byte(value), 0o600))
			}
			var output bytes.Buffer
			target := &hookedWriter{output: &output}
			if change == "grow-before-open" {
				mutate()
			} else {
				target.hook = mutate // Header write occurs after the input's fstat.
			}
			writer := tarWriter{ctx: t.Context(), output: target}
			// When archived, then exactly the opened byte count is required.
			err = writer.writeEntries([]entry{item})
			if change == "shrink-after-open" {
				require.ErrorIs(t, err, io.ErrUnexpectedEOF)
				var failure *Error
				require.ErrorAs(t, err, &failure)
				require.True(t, failure.Invalid)
				return
			}
			require.NoError(t, err)
			reader := tar.NewReader(bytes.NewReader(output.Bytes()))
			_, err = reader.Next()
			require.NoError(t, err)
			content, err := io.ReadAll(reader)
			require.NoError(t, err)
			want := "original"
			if change == "grow-before-open" {
				want += " appended"
			}
			require.Equal(t, want, string(content))
		})
	}
}

func TestArchiveCancellationStopsBeforeFileContent(t *testing.T) {
	// Given cancellation at the first header write, before file bytes are read.
	root := fixtureTree(t, treeFixture{Files: map[string]string{"file": "secret"}})
	item, err := collectEntry(filepath.Join(root, "file"), "file")
	require.NoError(t, err)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	var output bytes.Buffer
	writer := tarWriter{ctx: ctx, output: &hookedWriter{output: &output, hook: cancel}}
	// When archiving, then the next operation preserves cancellation.
	err = writer.writeEntries([]entry{item})
	require.ErrorIs(t, err, context.Canceled)
	require.Len(t, output.Bytes(), tarBlock)
}

func TestArchiveOversizeRemovesTemporaryFile(t *testing.T) {
	// Given a sparse file too large to fit with its tar headers.
	root := fixtureTree(t, treeFixture{Files: map[string]string{"file": "x"}})
	filename := filepath.Join(root, "file")
	require.NoError(t, os.Truncate(filename, MaxBytes))
	item, err := collectEntry(filename, "file")
	require.NoError(t, err)
	temporary := t.TempDir()
	t.Setenv("TMPDIR", temporary)
	t.Setenv("TMP", temporary)
	// When preparing fails after creating output, then its partial tar is removed.
	result, err := archive(t.Context(), []entry{item}, "Dockerfile")
	require.Nil(t, result)
	var failure *Error
	require.ErrorAs(t, err, &failure)
	require.True(t, failure.Invalid)
	files, err := os.ReadDir(temporary)
	require.NoError(t, err)
	require.Empty(t, files)
}

func TestArchiveTemporaryRootIsNotCollected(t *testing.T) {
	// Given the temporary directory inside the context itself.
	root := fixtureTree(t, treeFixture{Files: map[string]string{"Dockerfile": "FROM scratch\n"}})
	t.Setenv("TMPDIR", root)
	t.Setenv("TMP", root)
	// When prepared, then collection finished before the temporary output existed.
	result, err := FromDirectory(t.Context(), root, "")
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, result.Close()) })
	require.Equal(t, []string{"Dockerfile"}, archiveNames(t, result.Path))
}

func TestDirectoryCancelledBeforeTraversal(t *testing.T) {
	// Given an already cancelled operation and a nonexistent source.
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	// When prepared, then cancellation wins before filesystem traversal.
	_, err := FromDirectory(ctx, filepath.Join(t.TempDir(), "missing"), "")
	require.ErrorIs(t, err, context.Canceled)
	var failure *Error
	require.ErrorAs(t, err, &failure)
	require.False(t, failure.Invalid)
}

func TestDirectoryPermissionRefusal(t *testing.T) {
	// Given an unreadable kept directory on systems enforcing mode bits.
	root := fixtureTree(t, treeFixture{Files: map[string]string{"Dockerfile": "FROM scratch", "dir/file": "private"}})
	directory := filepath.Join(root, "dir")
	require.NoError(t, os.Chmod(directory, 0))
	t.Cleanup(func() { require.NoError(t, os.Chmod(directory, 0o755)) })
	if _, err := os.ReadDir(directory); !errors.Is(err, os.ErrPermission) {
		t.Skip("filesystem/user bypasses directory mode bits")
	}
	// When prepared, then the permission failure is invalid input.
	_, err := FromDirectory(t.Context(), root, "")
	var failure *Error
	require.ErrorAs(t, err, &failure)
	require.True(t, failure.Invalid)
}
