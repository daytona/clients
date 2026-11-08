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
	registerFields[request](t)
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
	Env             map[string]string `json:"env,omitempty"`
	Labels          map[string]string `json:"labels,omitempty"`
	LifecyclePolicy *casePolicy       `json:"lifecycle_policy,omitempty"`
	Volumes         []caseVolume      `json:"volumes,omitempty"`
	Options         map[string]casePolicy
}

// Keys name request fields exactly, at any depth, as the API matches them: a
// key that differs only in case is unknown, with a hint naming the field.
func TestDecode_keysAreExact(t *testing.T) {
	registerFields[caseRequest](t)
	for _, body := range []string{
		`{"CORES_MAX":0.5}`,
		`{"cores_max":0.5,"CORES_MAX":0.75}`,
		`{"lifecycle_policy":{"On_Timeout":"stop"}}`,
		`{"volumes":[{"volume":"a","Mount_Path":"/m"}]}`,
		`{"Options":{"x":{"ON_TIMEOUT":"b"}}}`,
		`{"ſecrets":["b"]}`,
	} {
		b, err := loadBody(t, body)
		if err != nil {
			t.Fatalf("Load %s: %v", body, err)
		}
		_, err = Decode[caseRequest](b)
		if cerr.ExitCode(err) != cerr.ExitUsage || !strings.Contains(err.Error(), "unknown field") {
			t.Fatalf("Decode %s: %v", body, err)
		}
	}
	b, _ := loadBody(t, `{"lifecycle_policy":{"On_Timeout":"stop"}}`)
	_, err := Decode[caseRequest](b)
	if hint := cerr.DetailsOf(err).Hint; !strings.Contains(hint, `"on_timeout"`) {
		t.Fatalf("hint %q", hint)
	}
}

// Map keys are the caller's own names: keys that differ only in case are
// distinct and decode as given.
func TestDecode_mapKeysAreKeptAsGiven(t *testing.T) {
	registerFields[caseRequest](t)
	b, err := loadBody(t, `{"env":{"PATH":"1","path":"2"},"labels":{"a":"1","A":"2"},"Options":{"Any":{"on_timeout":"x"}}}`)
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

// A type without a table is a bug, reported rather than decoded unchecked.
func TestDecode_needsAFieldTable(t *testing.T) {
	type unregistered struct{}
	if _, err := Decode[unregistered](Body{}); cerr.ExitCode(err) != cerr.ExitFailure {
		t.Fatalf("%v", err)
	}
}
