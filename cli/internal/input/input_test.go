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

// A flag overrides its --file field whatever the file key's capitalization.
func TestSet_replacesCaseVariantsOfTheKey(t *testing.T) {
	b := Body{"Timeout_Seconds": nil, "other": 1}
	b.Set("timeout_seconds", 60)
	if len(b) != 2 || b["timeout_seconds"] != 60 || b["other"] != 1 {
		t.Fatalf("got %v", b)
	}
	if err := RejectNulls(b, "timeout_seconds"); err != nil {
		t.Fatalf("the overridden null must not be rejected: %v", err)
	}
}

// Decode matches keys case-insensitively, so the null guard must too.
func TestRejectNulls_matchesKeysCaseInsensitively(t *testing.T) {
	err := RejectNulls(Body{"Timeout_Seconds": nil}, "timeout_seconds")
	if err == nil || !strings.Contains(err.Error(), "Timeout_Seconds") {
		t.Fatalf("expected null rejection, got %v", err)
	}
	type req struct {
		TimeoutSeconds *int64 `json:"timeout_seconds,omitempty"`
	}
	if _, err := Decode[req](Body{"Timeout_Seconds": nil}); err != nil {
		t.Fatalf("Decode accepts the case-folded key (why the guard must match it): %v", err)
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
