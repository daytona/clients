// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package input

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
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

func loadBody(t *testing.T, body string) (Body, error) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "body.json")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return Load(path, nil, false)
}

type caseVolume struct {
	Volume    string `json:"volume"`
	MountPath string `json:"mount_path"`
}

type casePolicy struct {
	OnTimeout *string `json:"on_timeout,omitempty"`
}

type caseRequest struct {
	CoresMax        *float64          `json:"cores_max,omitempty"`
	Secrets         []string          `json:"secrets,omitempty"`
	Env             map[string]string `json:"env,omitempty"`
	Labels          map[string]string `json:"labels,omitempty"`
	LifecyclePolicy *casePolicy       `json:"lifecycle_policy,omitempty"`
	Volumes         []caseVolume      `json:"volumes,omitempty"`
	Options         map[string]casePolicy
}

// Two keys that fill one struct field are refused: decoding keeps only one.
func TestCaseVariantsOfAFieldAreRefused(t *testing.T) {
	for _, body := range []string{
		`{"cores_max":0.5,"CORES_MAX":0.75}`,
		`{"secrets":["a"],"ſecrets":["b"]}`, // encoding/json folds ſ to s
	} {
		if _, err := loadBody(t, body); cerr.ExitCode(err) != cerr.ExitUsage {
			t.Fatalf("Load %s: %v", body, err)
		}
	}
	for _, body := range []string{
		`{"lifecycle_policy":{"on_timeout":"delete","On_Timeout":"stop"}}`,
		`{"volumes":[{"volume":"a","Volume":"b"}]}`,
		`{"Options":{"x":{"on_timeout":"a","ON_TIMEOUT":"b"}}}`,
	} {
		b, err := loadBody(t, body)
		if err != nil {
			t.Fatalf("Load %s: %v", body, err)
		}
		if _, err := Decode[caseRequest](b); cerr.ExitCode(err) != cerr.ExitUsage || !strings.Contains(err.Error(), "name the same field") {
			t.Fatalf("Decode %s: %v", body, err)
		}
	}
}

// Unknown keys are Decode's to report, not a case conflict.
func TestUnknownCaseVariantsAreReportedAsUnknown(t *testing.T) {
	b, err := loadBody(t, `{"lifecycle_policy":{"bogus":1,"BOGUS":2}}`)
	if err != nil {
		t.Fatal(err)
	}
	_, err = Decode[caseRequest](b)
	if cerr.ExitCode(err) != cerr.ExitUsage || !strings.Contains(err.Error(), "unknown field") {
		t.Fatalf("%v", err)
	}
}

// Map keys are the user's names: keys that differ only in case are distinct.
func TestCaseVariantsOfMapKeysAreKept(t *testing.T) {
	b, err := loadBody(t, `{"env":{"PATH":"1","path":"2"},"labels":{"a":"1","A":"2"}}`)
	if err != nil {
		t.Fatal(err)
	}
	req, err := Decode[caseRequest](b)
	if err != nil {
		t.Fatal(err)
	}
	if req.Env["PATH"] != "1" || req.Env["path"] != "2" || req.Labels["a"] != "1" || req.Labels["A"] != "2" {
		t.Fatalf("decoded %+v", req)
	}
}

func TestFoldKeyMatchesEqualFold(t *testing.T) {
	for _, pair := range [][2]string{{"secrets", "ſecrets"}, {"kelvin", "\u212aelvin"}, {"cores_max", "CORES_MAX"}, {"ǅ", "ǆ"}} {
		if foldKey(pair[0]) != foldKey(pair[1]) || !strings.EqualFold(pair[0], pair[1]) {
			t.Fatalf("%q %q", pair[0], pair[1])
		}
	}
	if foldKey("a_b") == foldKey("ab") {
		t.Fatal("distinct names folded together")
	}
}
