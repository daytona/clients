// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package input

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/pflag"
)

func TestLoadAndOverride(t *testing.T) {
	p := filepath.Join(t.TempDir(), "b.json")
	_ = os.WriteFile(p, []byte(`{"image":"python:3.12","cpu":2,"labels":{"a":"b"}}`), 0o600)
	b, err := Load(p, nil, false)
	if err != nil {
		t.Fatal(err)
	}
	fs := pflag.NewFlagSet("t", pflag.ContinueOnError)
	cpu := fs.Int("cpu", 1, "")
	mem := fs.Int("memory", 512, "")
	_ = fs.Parse([]string{"--cpu", "4"})
	b.SetIfChanged(fs, "cpu", "cpu", *cpu)
	b.SetIfChanged(fs, "memory", "memory_mib", *mem)
	if b["cpu"] != 4 {
		t.Fatalf("flag should override file: %v", b["cpu"])
	}
	if _, ok := b["memory_mib"]; ok {
		t.Fatal("unchanged flag default must not be written")
	}
	if b["image"] != "python:3.12" {
		t.Fatal("file value lost")
	}
	if err := b.Require("image", "--image"); err != nil {
		t.Fatal(err)
	}
	if err := b.Require("name", "--name"); err == nil {
		t.Fatal("missing should fail")
	}
}

func TestLoadStdin(t *testing.T) {
	b, err := Load("-", strings.NewReader(`{"count":1}`), false)
	if err != nil || b["count"] == nil {
		t.Fatal(err, b)
	}
	if _, err := Load("-", strings.NewReader(""), true); err == nil {
		t.Fatal("stdin conflict should fail")
	}
	if _, err := Load("-", strings.NewReader(`[1]`), false); err == nil {
		t.Fatal("array is not an object")
	}
	if _, err := Load("-", strings.NewReader(`null`), false); err == nil {
		t.Fatal("null is not an object")
	}
}

func TestLoad_rejectsTrailingJSON(t *testing.T) {
	p := filepath.Join(t.TempDir(), "b.json")
	_ = os.WriteFile(p, []byte(`{"image":"python:3.12"} {}`), 0o600)
	if _, err := Load(p, nil, false); err == nil {
		t.Fatal("expected trailing data error")
	}
}

func TestDecode_rejectsUnknownFields(t *testing.T) {
	type request struct {
		Image string `json:"image"`
	}
	_, err := Decode[request](Body{"image": "python:3.12", "unknown_field": "example"})
	if err == nil || !strings.Contains(err.Error(), "unknown_field") {
		t.Fatalf("expected unknown field error, got %v", err)
	}
}

func TestRejectNulls_rejectsExplicitNulls(t *testing.T) {
	err := RejectNulls(Body{"name": nil}, "name")
	if err == nil || !strings.Contains(err.Error(), "not nullable") {
		t.Fatalf("expected null rejection, got %v", err)
	}

	if err := RejectNulls(Body{"name": "ok"}, "name"); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestKV(t *testing.T) {
	m, err := KV([]string{"A=1", "B=x=y"}, "--env")
	if err != nil || m["A"] != "1" || m["B"] != "x=y" {
		t.Fatal(m, err)
	}
	if _, err := KV([]string{"novalue"}, "--env"); err == nil {
		t.Fatal()
	}
}
