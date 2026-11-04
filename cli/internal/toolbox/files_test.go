// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
)

func TestParseTransferSpec(t *testing.T) {
	t.Parallel()
	spec, ok := ParseTransferSpec("sbx:/tmp/file.txt")
	if !ok || spec.SandboxID != "sbx" || spec.Path != "/tmp/file.txt" {
		t.Fatalf("unexpected parse result: %#v %v", spec, ok)
	}
	if _, ok := ParseTransferSpec("/tmp/file.txt"); ok {
		t.Fatal("local path parsed as remote")
	}
	if _, ok := ParseTransferSpec("sbx:relative"); ok {
		t.Fatal("relative remote path parsed as valid")
	}
	if _, ok := ParseTransferSpec("C:/dir/file.txt"); ok {
		t.Fatal("windows drive path parsed as remote")
	}
	if _, ok := ParseTransferSpec(`d:\\logs\\out.txt`); ok {
		t.Fatal("windows backslash drive path parsed as remote")
	}
}

func TestWriteLogFrames_decodesBase64(t *testing.T) {
	t.Parallel()
	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	frames := []tbgen.LogFrameDto{{Channel: "stdout", Data: "aGVsbG8="}, {Channel: "stderr", Data: "d29ybGQ="}}
	if err := WriteLogFrames(frames, "base64", stdout, stderr); err != nil {
		t.Fatal(err)
	}
	if stdout.String() != "hello" || stderr.String() != "world" {
		t.Fatalf("stdout=%q stderr=%q", stdout.String(), stderr.String())
	}
}

func TestExtractArchive_roundTripsDirectory(t *testing.T) {
	t.Parallel()
	src := t.TempDir()
	if err := os.WriteFile(filepath.Join(src, "a.txt"), []byte("alpha"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(src, "dir"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(src, "dir", "b.txt"), []byte("beta"), 0o600); err != nil {
		t.Fatal(err)
	}
	archive, err := ArchiveDirectory(src)
	if err != nil {
		t.Fatal(err)
	}
	defer archive.Close()
	dst := t.TempDir()
	if err := ExtractArchive(archive, dst); err != nil {
		t.Fatal(err)
	}
	gotA, err := os.ReadFile(filepath.Join(dst, "a.txt"))
	if err != nil {
		t.Fatal(err)
	}
	gotB, err := os.ReadFile(filepath.Join(dst, "dir", "b.txt"))
	if err != nil {
		t.Fatal(err)
	}
	if string(gotA) != "alpha" || string(gotB) != "beta" {
		t.Fatalf("round trip mismatch: %q %q", string(gotA), string(gotB))
	}
}
