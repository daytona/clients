// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDocsDirCreatesDirectory(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "nested", "docs")
	_, errb, code := run(t, cfgDir(t), nil, "docs", "--dir", dir)
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	if _, err := os.Stat(filepath.Join(dir, "daytona_sandbox_create.md")); err != nil {
		t.Fatalf("per-command markdown missing: %v", err)
	}
}
