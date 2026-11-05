// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build windows

package toolbox

import (
	"os"
	"path/filepath"
	"testing"
)

func TestOpenRegularIn_createsRegularFile_whenPathIsPlainFile(t *testing.T) {
	t.Parallel()

	// Given
	dir := t.TempDir()
	root, err := os.OpenRoot(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()

	// When
	file, err := openRegularIn(root, "plain.txt", filepath.Join(dir, "plain.txt"), 0o644)
	if err != nil {
		t.Fatalf("openRegularIn(): %v", err)
	}
	if _, err := file.WriteString("ok"); err != nil {
		t.Fatalf("WriteString(): %v", err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}

	// Then
	content, err := os.ReadFile(filepath.Join(dir, "plain.txt"))
	if err != nil {
		t.Fatalf("ReadFile(): %v", err)
	}
	if string(content) != "ok" {
		t.Fatalf("unexpected content %q", content)
	}
}

func TestOpenRegularIn_givesExistingHardLinkItsOwnFile(t *testing.T) {
	t.Parallel()

	// Given
	dir := t.TempDir()
	original, linked := filepath.Join(dir, "original.txt"), filepath.Join(dir, "linked.txt")
	if err := os.WriteFile(original, []byte("original"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Link(original, linked); err != nil {
		t.Skipf("hard links unavailable: %v", err)
	}
	root, err := os.OpenRoot(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()

	// When
	file, err := openRegularIn(root, "linked.txt", linked, 0o644)
	if err != nil {
		t.Fatalf("openRegularIn(): %v", err)
	}
	if _, err := file.WriteString("new"); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}

	// Then
	if got, _ := os.ReadFile(original); string(got) != "original" {
		t.Fatalf("hard-linked sibling changed: %q", got)
	}
	if got, _ := os.ReadFile(linked); string(got) != "new" {
		t.Fatalf("linked content %q", got)
	}
}
