// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build windows

package toolbox

import (
	"os"
	"path/filepath"
	"testing"
)

func TestOpenRegularNoFollow_createsRegularFile_whenPathIsPlainFile(t *testing.T) {
	t.Parallel()

	// Given
	target := filepath.Join(t.TempDir(), "plain.txt")

	// When
	file, err := openRegularNoFollow(target, 0o644)
	if err != nil {
		t.Fatalf("openRegularNoFollow(): %v", err)
	}
	defer file.Close()
	if _, err := file.WriteString("ok"); err != nil {
		t.Fatalf("WriteString(): %v", err)
	}

	// Then
	content, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("ReadFile(): %v", err)
	}
	if string(content) != "ok" {
		t.Fatalf("unexpected content %q", content)
	}
}
