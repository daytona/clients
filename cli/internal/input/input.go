// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

// Package input merges a --file JSON body with explicit flags: the
// file is the base, flags the caller marked as changed override it
// field by field. `--file -` reads stdin.
package input

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/spf13/pflag"

	"github.com/daytona/clients/cli/internal/cerr"
)

// Body is a JSON object under construction.
type Body map[string]any

// Load reads --file (path or "-") into a Body; empty path → empty Body.
// stdinIsPayload refuses "-" when stdin already carries a payload stream.
func Load(path string, stdin io.Reader, stdinIsPayload bool) (Body, error) {
	if path == "" {
		return Body{}, nil
	}
	var r io.Reader
	if path == "-" {
		if stdinIsPayload {
			return nil, cerr.Usage("--file - conflicts with reading the payload from stdin")
		}
		r = stdin
	} else {
		f, err := os.Open(path)
		if err != nil {
			return nil, cerr.Usage("open --file %s: %v", path, err)
		}
		defer f.Close()
		r = f
	}
	var b Body
	dec := json.NewDecoder(r)
	dec.UseNumber()
	if err := dec.Decode(&b); err != nil {
		return nil, cerr.Usage("--file %s: invalid JSON object: %v", path, err)
	}
	if b == nil {
		return nil, cerr.Usage("--file %s: JSON object required", path)
	}
	if err := dec.Decode(new(struct{})); err != io.EOF {
		return nil, cerr.Usage("--file %s: trailing data after the JSON object", path)
	}
	return b, nil
}

// Set writes a field unconditionally. Decode matches keys
// case-insensitively, so Set also drops any case variant of key (from --file)
// that would otherwise compete with the value written here.
func (b Body) Set(key string, v any) {
	for existing := range b {
		if existing != key && strings.EqualFold(existing, key) {
			delete(b, existing)
		}
	}
	b[key] = v
}

// SetIfChanged writes a field only when the flag was explicitly given, so a
// flag's default never clobbers a value from --file.
func (b Body) SetIfChanged(fs *pflag.FlagSet, flag, key string, v any) {
	if fs.Changed(flag) {
		b.Set(key, v)
	}
}

// Require fails with a usage error when key is absent after the merge.
func (b Body) Require(key, flag string) error {
	if v, ok := b[key]; !ok || v == nil || v == "" {
		return cerr.Usage("%s is required", flag).WithHint("pass %s or set %q in --file", flag, key)
	}
	return nil
}

// RejectNulls refuses an explicit null for any of fields. Keys match
// case-insensitively, as Decode does: `"Labels": null` would otherwise pass
// here and then decode into the `labels` field as omitted.
func RejectNulls(b Body, fields ...string) error {
	for _, field := range fields {
		for key, value := range b {
			if value == nil && strings.EqualFold(key, field) {
				return cerr.Usage("%q is not nullable; omit it to keep the current value", key)
			}
		}
	}
	return nil
}

// KV parses KEY=VALUE pairs (env, labels).
func KV(pairs []string, what string) (map[string]string, error) {
	out := map[string]string{}
	for _, p := range pairs {
		k, v, ok := cut(p)
		if !ok || k == "" {
			return nil, cerr.Usage("%s wants KEY=VALUE, got %q", what, p)
		}
		out[k] = v
	}
	return out, nil
}

func cut(s string) (string, string, bool) {
	for i := 0; i < len(s); i++ {
		if s[i] == '=' {
			return s[:i], s[i+1:], true
		}
	}
	return s, "", false
}

// String renders the body for --debug.
func (b Body) String() string {
	j, _ := json.Marshal(b)
	return fmt.Sprintf("%s", j)
}

// Decode marshals the merged body into a typed request struct, so the server's
// schema (via the generated types) validates what the flags and --file
// produced. Explicit JSON nulls are not representable once a generated
// `omitempty` pointer field is materialized into the typed request, so callers
// must reject them before Decode when the API distinguishes null from omitted.
// One implementation for every command family.
func Decode[T any](b Body) (T, error) {
	var out T
	raw, err := json.Marshal(b)
	if err != nil {
		return out, fmt.Errorf("encode request body: %w", err)
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&out); err != nil {
		return out, cerr.Usage("invalid request body: %v", err)
	}
	return out, nil
}
