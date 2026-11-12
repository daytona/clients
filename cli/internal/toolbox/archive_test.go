// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"archive/tar"
	"bytes"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeTar(t *testing.T, entries ...*tar.Header) *bytes.Buffer {
	t.Helper()
	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	for _, h := range entries {
		if err := tw.WriteHeader(h); err != nil {
			t.Fatal(err)
		}
		if h.Typeflag == tar.TypeReg {
			_, _ = tw.Write([]byte("x"))
		}
	}
	_ = tw.Close()
	return &buf
}

func TestArchiveRoundTripWithSymlink(t *testing.T) {
	src := t.TempDir()
	_ = os.MkdirAll(filepath.Join(src, "d"), 0o755)
	_ = os.WriteFile(filepath.Join(src, "d", "f.txt"), []byte("hello"), 0o644)
	if err := os.Symlink("d/f.txt", filepath.Join(src, "link")); err != nil {
		t.Skip("symlinks unsupported")
	}
	r, err := ArchiveDirectory(src)
	if err != nil {
		t.Fatal(err)
	}
	dst := t.TempDir()
	if err := ExtractArchive(r, dst); err != nil {
		t.Fatal(err)
	}
	b, err := os.ReadFile(filepath.Join(dst, "d", "f.txt"))
	if err != nil || string(b) != "hello" {
		t.Fatal(err, string(b))
	}
	got, err := os.Readlink(filepath.Join(dst, "link"))
	if err != nil || got != "d/f.txt" {
		t.Fatalf("symlink not recreated: %q %v", got, err)
	}
}

func TestExtractRejectsEscapes(t *testing.T) {
	dst := t.TempDir()
	// 1. lexical traversal
	err := ExtractArchive(writeTar(t, &tar.Header{Name: "../evil", Typeflag: tar.TypeReg, Mode: 0o644, Size: 1}), dst)
	if err == nil || !strings.Contains(err.Error(), "escapes") {
		t.Fatalf("traversal accepted: %v", err)
	}
	// 2. symlink pointing outside dst
	err = ExtractArchive(writeTar(t, &tar.Header{Name: "out", Typeflag: tar.TypeSymlink, Linkname: "../../etc"}), dst)
	if err == nil || !strings.Contains(err.Error(), "refused") {
		t.Fatalf("outward symlink accepted: %v", err)
	}
	// 2b. a parent-relative link that would stay INSIDE dst is refused too:
	// ".." in a link target is the escape vector, so the policy is "never",
	// not "only when it escapes" — the archiver side documents this.
	err = ExtractArchive(writeTar(t,
		&tar.Header{Name: "sub/", Typeflag: tar.TypeDir, Mode: 0o755},
		&tar.Header{Name: "sub/up", Typeflag: tar.TypeSymlink, Linkname: "../d"}), dst)
	if err == nil || !strings.Contains(err.Error(), "refused") {
		t.Fatalf("parent-relative link accepted: %v", err)
	}
	// 3. writing through a pre-existing symlinked directory under dst
	outside := t.TempDir()
	requireSymlinkOrSkip(t, outside, filepath.Join(dst, "outside-link"))
	err = ExtractArchive(writeTar(t, &tar.Header{Name: "outside-link/pwned", Typeflag: tar.TypeReg, Mode: 0o644, Size: 1}), dst)
	if err == nil || !strings.Contains(err.Error(), "traverses a symlink") {
		t.Fatalf("write through symlinked dir accepted: %v", err)
	}
	if _, statErr := os.Stat(filepath.Join(outside, "pwned")); statErr == nil {
		t.Fatal("file escaped into the symlink target")
	}
	// 3b. a NUL in the name is refused by the guard, not by the OS (Go's tar
	// writer cannot even emit such a header, so exercise the guard directly —
	// a hand-crafted stream can carry one).
	if _, err := safeTarget(dst, "a\x00b"); err == nil || !strings.Contains(err.Error(), "NUL") {
		t.Fatalf("NUL name accepted: %v", err)
	}
	// 3c. two-step escape in ONE archive: an in-tree symlink to a sibling
	// directory, then a second link whose target goes THROUGH the first one
	// and out of the root — caught only when the destination is resolved on
	// the real filesystem, not lexically.
	dst2 := t.TempDir()
	outside2 := t.TempDir()
	_ = os.MkdirAll(filepath.Join(dst2, "real"), 0o755)
	if err := os.Symlink(outside2, filepath.Join(dst2, "real", "out")); err != nil {
		t.Skip("symlinks unsupported")
	}
	err = ExtractArchive(writeTar(t, &tar.Header{Name: "hop", Typeflag: tar.TypeSymlink, Linkname: "real/out/secret"}), dst2)
	if err == nil || !strings.Contains(err.Error(), "escapes") {
		t.Fatalf("link through a pre-existing symlink accepted: %v", err)
	}
	// 4. absolute names are re-rooted, not honoured
	if err := ExtractArchive(writeTar(t, &tar.Header{Name: "/abs.txt", Typeflag: tar.TypeReg, Mode: 0o644, Size: 1}), dst); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(dst, "abs.txt")); err != nil {
		t.Fatal("absolute entry should land under dst")
	}
	_ = io.EOF
}

func TestExtractArchive_recreatesHardLinksInsideDestination(t *testing.T) {
	dst := t.TempDir()
	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	header := &tar.Header{Name: "file.txt", Typeflag: tar.TypeReg, Mode: 0o644, Size: int64(len("payload"))}
	if err := tw.WriteHeader(header); err != nil {
		t.Fatal(err)
	}
	if _, err := tw.Write([]byte("payload")); err != nil {
		t.Fatal(err)
	}
	if err := tw.WriteHeader(&tar.Header{Name: "copy.txt", Typeflag: tar.TypeLink, Linkname: "file.txt"}); err != nil {
		t.Fatal(err)
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	if err := ExtractArchive(bytes.NewReader(buf.Bytes()), dst); err != nil {
		t.Fatal(err)
	}
	original, err := os.ReadFile(filepath.Join(dst, "file.txt"))
	if err != nil {
		t.Fatal(err)
	}
	copyBytes, err := os.ReadFile(filepath.Join(dst, "copy.txt"))
	if err != nil {
		t.Fatal(err)
	}
	if string(original) != "payload" || string(copyBytes) != "payload" {
		t.Fatalf("unexpected hard-link content: %q %q", original, copyBytes)
	}
}

// A symlink planted in the destination — at a file's path or as a parent
// directory — must not be written through.
func TestExtractArchive_refusesSymlinkPlantedInDestination(t *testing.T) {
	victim := filepath.Join(t.TempDir(), "victim.txt")
	if err := os.WriteFile(victim, []byte("original"), 0o644); err != nil {
		t.Fatal(err)
	}
	archive := func(name string) io.Reader {
		var buf bytes.Buffer
		tw := tar.NewWriter(&buf)
		_ = tw.WriteHeader(&tar.Header{Name: name, Mode: 0o644, Size: 5, Typeflag: tar.TypeReg})
		_, _ = tw.Write([]byte("pwned"))
		_ = tw.Close()
		return &buf
	}
	// 1. symlink at the file path
	dst := t.TempDir()
	requireSymlinkOrSkip(t, victim, filepath.Join(dst, "a.txt"))
	if err := ExtractArchive(archive("a.txt"), dst); err == nil {
		t.Fatal("expected refusal for a symlinked file path")
	}
	// 2. symlinked parent directory
	dst2 := t.TempDir()
	requireSymlinkOrSkip(t, filepath.Dir(victim), filepath.Join(dst2, "sub"))
	if err := ExtractArchive(archive("sub/victim.txt"), dst2); err == nil {
		t.Fatal("expected refusal for a symlinked parent")
	}
	got, _ := os.ReadFile(victim)
	if string(got) != "original" {
		t.Fatalf("victim overwritten: %q", got)
	}
}

func TestExtractArchive_refusesParentSymlinkSwap_afterValidation(t *testing.T) {
	victimDir := t.TempDir()
	victim := filepath.Join(victimDir, "victim.txt")
	if err := os.WriteFile(victim, []byte("original"), 0o644); err != nil {
		t.Fatal(err)
	}

	dst := t.TempDir()
	if err := os.Mkdir(filepath.Join(dst, "safe"), 0o755); err != nil {
		t.Fatal(err)
	}

	archiveBeforeRegularOpen = func(target string) {
		if filepath.Base(target) != "victim.txt" {
			return
		}
		parent := filepath.Dir(target)
		if err := os.Remove(parent); err != nil {
			t.Fatalf("remove parent: %v", err)
		}
		requireSymlinkOrSkip(t, victimDir, parent)
	}
	defer func() { archiveBeforeRegularOpen = nil }()

	err := ExtractArchive(writeTar(t, &tar.Header{Name: "safe/victim.txt", Typeflag: tar.TypeReg, Mode: 0o644, Size: 1}), dst)
	if err == nil {
		t.Fatal("expected refusal for parent symlink swap")
	}
	got, readErr := os.ReadFile(victim)
	if readErr != nil {
		t.Fatal(readErr)
	}
	if string(got) != "original" {
		t.Fatalf("victim overwritten: %q", got)
	}
}

// The write lands in the parent directory opened during validation even when
// that directory is renamed away and replaced by a symlink before the file is
// created: extraction never re-resolves the parent by path.
func TestExtractArchive_writesIntoPinnedParent_whenParentIsSwappedAfterOpen(t *testing.T) {
	victimDir := t.TempDir()
	victim := filepath.Join(victimDir, "victim.txt")
	if err := os.WriteFile(victim, []byte("original"), 0o644); err != nil {
		t.Fatal(err)
	}
	dst := t.TempDir()
	if err := os.Mkdir(filepath.Join(dst, "safe"), 0o755); err != nil {
		t.Fatal(err)
	}
	moved := filepath.Join(dst, "safe-moved")
	called, swapped := false, false
	archiveAfterParentPinned = func(target string) {
		if filepath.Base(target) != "victim.txt" {
			return
		}
		called = true
		parent := filepath.Dir(target)
		if err := os.Rename(parent, moved); err != nil {
			t.Fatalf("rename the pinned parent: %v", err)
		}
		requireSymlinkOrSkip(t, victimDir, parent)
		swapped = true
	}
	defer func() { archiveAfterParentPinned = nil }()

	err := ExtractArchive(writeTar(t, &tar.Header{Name: "safe/victim.txt", Typeflag: tar.TypeReg, Mode: 0o644, Size: 1}), dst)

	if !called {
		t.Fatal("extraction must pin the parent directory before creating the file")
	}
	got, readErr := os.ReadFile(victim)
	if readErr != nil {
		t.Fatal(readErr)
	}
	if string(got) != "original" {
		t.Fatalf("victim overwritten through the swapped parent: %q", got)
	}
	if !swapped {
		t.Fatal("the parent swap did not happen")
	}
	if err != nil {
		t.Fatalf("ExtractArchive(): %v", err)
	}
	pinned, readErr := os.ReadFile(filepath.Join(moved, "victim.txt"))
	if readErr != nil {
		t.Fatalf("the entry must land in the pinned (renamed) parent: %v", readErr)
	}
	if string(pinned) != "x" {
		t.Fatalf("pinned entry content %q", pinned)
	}
}
