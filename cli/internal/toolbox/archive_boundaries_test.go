// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"archive/tar"
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func requireSymlinkOrSkip(t *testing.T, target string, link string) {
	t.Helper()
	if err := os.Symlink(target, link); err != nil {
		if runtime.GOOS == "windows" && (errors.Is(err, os.ErrPermission) || strings.Contains(strings.ToLower(err.Error()), "privilege")) {
			t.Skip("symlink privilege unavailable")
		}
		t.Fatalf("Symlink(%q, %q): %v", target, link, err)
	}
}

func chdir(t *testing.T, dir string) {
	t.Helper()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(dir); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.Chdir(wd); err != nil {
			t.Fatalf("restore wd: %v", err)
		}
	})
}

// Any ".." in an entry name is refused — even inside a component such as
// `report..json` (a deliberate over-approximation so the zip-slip barrier is
// one unconditional check static analysers recognise).
func TestExtractArchive_refusesAnyDotDotInEntryName(t *testing.T) {
	for _, name := range []string{"report..json", "foo..bar/file", "../x", "a/../../b"} {
		var buf bytes.Buffer
		tw := tar.NewWriter(&buf)
		_ = tw.WriteHeader(&tar.Header{Name: name, Mode: 0o644, Size: 1, Typeflag: tar.TypeReg})
		_, _ = tw.Write([]byte("x"))
		_ = tw.Close()
		if err := ExtractArchive(&buf, t.TempDir()); err == nil {
			t.Fatalf("%q must be refused", name)
		}
	}
}

func TestExtractArchive_allowsDotDestination(t *testing.T) {
	dst := t.TempDir()
	chdir(t, dst)

	err := ExtractArchive(writeTar(t,
		&tar.Header{Name: "dot.txt", Typeflag: tar.TypeReg, Mode: 0o644, Size: 1},
	), ".")
	if err != nil {
		t.Fatalf("ExtractArchive(): %v", err)
	}

	if _, err := os.Stat(filepath.Join(dst, "dot.txt")); err != nil {
		t.Fatalf("Stat(dot.txt): %v", err)
	}
}

func TestExtractArchive_extractsSymlinkEntries_whenDestinationIsRelative(t *testing.T) {
	dstRoot := t.TempDir()
	chdir(t, dstRoot)

	err := ExtractArchive(writeTar(t,
		&tar.Header{Name: "sub/", Typeflag: tar.TypeDir, Mode: 0o755},
		&tar.Header{Name: "sub/file.txt", Typeflag: tar.TypeReg, Mode: 0o644, Size: 1},
		&tar.Header{Name: "link.txt", Typeflag: tar.TypeSymlink, Linkname: "sub/file.txt"},
	), "out")
	if err != nil {
		t.Fatalf("ExtractArchive(): %v", err)
	}

	got, err := os.Readlink(filepath.Join(dstRoot, "out", "link.txt"))
	if err != nil {
		t.Fatalf("Readlink(): %v", err)
	}
	if got != "sub/file.txt" {
		t.Fatalf("unexpected symlink target %q", got)
	}
}

func TestExtractArchive_replacesExistingHardLinkWithoutMutatingSibling(t *testing.T) {
	dst := t.TempDir()
	original := filepath.Join(dst, "original.txt")
	linked := filepath.Join(dst, "linked.txt")
	if err := os.WriteFile(original, []byte("original"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Link(original, linked); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	content := []byte("new-data")
	if err := tw.WriteHeader(&tar.Header{Name: "linked.txt", Typeflag: tar.TypeReg, Mode: 0o600, Size: int64(len(content))}); err != nil {
		t.Fatal(err)
	}
	if _, err := tw.Write(content); err != nil {
		t.Fatal(err)
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}

	if err := ExtractArchive(bytes.NewReader(buf.Bytes()), dst); err != nil {
		t.Fatalf("ExtractArchive(): %v", err)
	}

	originalBytes, err := os.ReadFile(original)
	if err != nil {
		t.Fatal(err)
	}
	linkedBytes, err := os.ReadFile(linked)
	if err != nil {
		t.Fatal(err)
	}
	if string(originalBytes) != "original" {
		t.Fatalf("original content changed: %q", originalBytes)
	}
	if string(linkedBytes) != string(content) {
		t.Fatalf("linked content mismatch: %q", linkedBytes)
	}
}

func TestArchiveDirectory_returnsErrorForRegularFileSource(t *testing.T) {
	src := filepath.Join(t.TempDir(), "file.txt")
	if err := os.WriteFile(src, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}

	r, err := ArchiveDirectory(src)
	if err == nil {
		_ = r.Close()
		t.Fatal("expected error for non-directory source")
	}
	if !strings.Contains(err.Error(), "not a directory") {
		t.Fatalf("unexpected error: %v", err)
	}
}

// A directory entry that maps to the destination root (".", "/", "") may not
// restyle the caller's directory: its mode is skipped, children still extract;
// a regular file mapping to the root is refused.
func TestExtractArchive_rootMappedEntriesNeverRestyleDestination(t *testing.T) {
	dst := t.TempDir()
	if err := os.Chmod(dst, 0o755); err != nil {
		t.Fatal(err)
	}
	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	_ = tw.WriteHeader(&tar.Header{Name: "./", Mode: 0o777, Typeflag: tar.TypeDir})
	_ = tw.WriteHeader(&tar.Header{Name: "/", Mode: 0o777, Typeflag: tar.TypeDir})
	_ = tw.WriteHeader(&tar.Header{Name: "./a.txt", Mode: 0o644, Size: 2, Typeflag: tar.TypeReg})
	_, _ = tw.Write([]byte("hi"))
	_ = tw.Close()
	if err := ExtractArchive(&buf, dst); err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(dst)
	if err != nil {
		t.Fatal(err)
	}
	// Windows reports directory perms as 0o777 regardless; the property under
	// test (the archive's 0o777 was NOT applied) is only observable on POSIX.
	if runtime.GOOS != "windows" && st.Mode().Perm() != 0o755 {
		t.Fatalf("destination mode restyled to %o", st.Mode().Perm())
	}
	if got, _ := os.ReadFile(filepath.Join(dst, "a.txt")); string(got) != "hi" {
		t.Fatalf("child not extracted: %q", got)
	}
	var bad bytes.Buffer
	tw = tar.NewWriter(&bad)
	_ = tw.WriteHeader(&tar.Header{Name: ".", Mode: 0o644, Size: 1, Typeflag: tar.TypeReg})
	_, _ = tw.Write([]byte("x"))
	_ = tw.Close()
	if err := ExtractArchive(&bad, t.TempDir()); err == nil {
		t.Fatal("a regular file mapping to the root must be refused")
	}
}
