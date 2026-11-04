// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build !windows

package toolbox

import (
	"archive/tar"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestExtractArchive_extractsSymlinks_whenDestinationParentIsSymlink(t *testing.T) {
	// Given a destination reached through a symlink, as with macOS /var.
	parent := t.TempDir()
	alias := filepath.Join(t.TempDir(), "alias")
	requireSymlinkOrSkip(t, parent, alias)
	dst := filepath.Join(alias, "out")
	archive := writeTar(t,
		&tar.Header{Name: "sub/file.txt", Typeflag: tar.TypeReg, Mode: 0o644, Size: 1},
		&tar.Header{Name: "link.txt", Typeflag: tar.TypeSymlink, Linkname: "sub/file.txt"},
		&tar.Header{Name: "sub/link.txt", Typeflag: tar.TypeSymlink, Linkname: "file.txt"},
		&tar.Header{Name: "dangling", Typeflag: tar.TypeSymlink, Linkname: "missing/file.txt"},
	)

	// When extracting relative symlinks at the root and in a subdirectory.
	err := ExtractArchive(archive, dst)

	// Then links retain their relative targets and resolve to extracted data.
	if err != nil {
		t.Fatalf("ExtractArchive(): %v", err)
	}
	for name, want := range map[string]string{
		"link.txt":     "sub/file.txt",
		"sub/link.txt": "file.txt",
		"dangling":     "missing/file.txt",
	} {
		got, err := os.Readlink(filepath.Join(parent, "out", name))
		if err != nil || got != want {
			t.Fatalf("Readlink(%s): got %q, want %q: %v", name, got, want, err)
		}
	}
	for _, name := range []string{"link.txt", "sub/link.txt"} {
		got, err := os.ReadFile(filepath.Join(parent, "out", name))
		if err != nil || string(got) != "x" {
			t.Fatalf("ReadFile(%s): got %q, want x: %v", name, got, err)
		}
	}
}

func TestExtractArchive_rejectsEscapes_whenDestinationParentIsSymlink(t *testing.T) {
	for _, header := range []*tar.Header{
		{Name: "../escape", Typeflag: tar.TypeReg, Mode: 0o644, Size: 1},
		{Name: "absolute", Typeflag: tar.TypeSymlink, Linkname: "/etc/passwd"},
		{Name: "parent", Typeflag: tar.TypeSymlink, Linkname: "../outside"},
		{Name: "hop", Typeflag: tar.TypeSymlink, Linkname: "outside/file.txt"},
		{Name: "outside/write.txt", Typeflag: tar.TypeReg, Mode: 0o644, Size: 1},
	} {
		t.Run(header.Name, func(t *testing.T) {
			// Given a symlinked destination parent and an outward link inside it.
			parent := t.TempDir()
			alias := filepath.Join(t.TempDir(), "alias")
			requireSymlinkOrSkip(t, parent, alias)
			dst := filepath.Join(alias, "out")
			if err := os.Mkdir(dst, 0o755); err != nil {
				t.Fatal(err)
			}
			outside := t.TempDir()
			requireSymlinkOrSkip(t, outside, filepath.Join(dst, "outside"))

			// When extracting an entry that escapes or traverses the outward link.
			err := ExtractArchive(writeTar(t, header), dst)

			// Then the entry is refused despite the allowed destination alias.
			if err == nil {
				t.Fatal("expected extraction to refuse escape")
			}
			require.NoFileExists(t, filepath.Join(outside, "write.txt"))
		})
	}
}

func TestArchiveExtractor_keepsOpenedRoot_whenDestinationIsSwapped(t *testing.T) {
	// Given an extractor pinned to the original destination directory.
	parent := t.TempDir()
	dst := filepath.Join(parent, "out")
	require.NoError(t, os.Mkdir(dst, 0o755))
	extractor, err := newArchiveExtractor(dst)
	require.NoError(t, err)
	defer extractor.Close()
	original := filepath.Join(parent, "original")
	require.NoError(t, os.Rename(dst, original))
	outside := t.TempDir()
	requireSymlinkOrSkip(t, outside, dst)

	// When writing after a path swap, all writes still use the pinned fd.
	f, err := extractor.OpenRegular("write.txt", filepath.Join(dst, "write.txt"), 0o644)
	require.NoError(t, err)
	require.NoError(t, f.Close())

	// Then nothing is written through the attacker's replacement symlink.
	require.NoFileExists(t, filepath.Join(outside, "write.txt"))
	require.FileExists(t, filepath.Join(original, "write.txt"))
}
