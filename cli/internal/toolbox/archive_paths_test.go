// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"os"
	"path/filepath"
	"testing"
)

func filesystemRoot(t *testing.T) string {
	t.Helper()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	return filepath.VolumeName(wd) + string(filepath.Separator)
}

func TestResolveArchiveTarget_acceptsChildrenOfFilesystemRoot(t *testing.T) {
	root := filesystemRoot(t)
	name := "daytona-archive-root-test-does-not-exist/file.txt"

	entry, err := resolveArchiveTarget(root, name)
	if err != nil {
		t.Fatalf("resolveArchiveTarget(%q, %q): %v", root, name, err)
	}

	if want := filepath.Join(root, filepath.FromSlash(name)); entry.target != want {
		t.Fatalf("target = %q, want %q", entry.target, want)
	}
	if want := filepath.FromSlash(name); entry.relPath != want {
		t.Fatalf("relPath = %q, want %q", entry.relPath, want)
	}
}

func TestDirPrefix_addsOneSeparator(t *testing.T) {
	root := filesystemRoot(t)
	base := filepath.Join(root, "srv", "dst")
	if got := dirPrefix(root); got != root {
		t.Fatalf("dirPrefix(%q) = %q, want %q", root, got, root)
	}
	if got, want := dirPrefix(base), base+string(filepath.Separator); got != want {
		t.Fatalf("dirPrefix(%q) = %q, want %q", base, got, want)
	}
}
