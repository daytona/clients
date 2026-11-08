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
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"

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
	if err := rejectTopLevelCaseVariants(b); err != nil {
		return nil, cerr.Usage("--file %s: %v", path, err)
	}
	return b, nil
}

// Decoding matches a JSON key to a struct field exactly or, failing that,
// case-insensitively, so two keys that differ only in case ("cores_max" and
// "CORES_MAX") fill the same field and one value is silently dropped. Load
// and Decode refuse such bodies. Keys of map-typed values (`env`, `labels`)
// are the user's own names and decode as given, so they may differ only in
// case.

// foldKey folds name the way encoding/json does when it matches a key to a
// field: foldKey(a) == foldKey(b) exactly when strings.EqualFold(a, b), so
// "secrets", "SECRETS" and "ſecrets" are one field.
func foldKey(name string) string {
	var b strings.Builder
	for _, r := range name {
		if r < utf8.RuneSelf {
			if 'a' <= r && r <= 'z' {
				r -= 'a' - 'A'
			}
			b.WriteRune(r)
			continue
		}
		for {
			next := unicode.SimpleFold(r)
			if next <= r {
				r = next
				break
			}
			r = next
		}
		b.WriteRune(r)
	}
	return b.String()
}

// rejectTopLevelCaseVariants refuses two spellings of one top-level key:
// every --file body is an object that decodes into a request struct.
func rejectTopLevelCaseVariants(b Body) error {
	seen := make(map[string]string, len(b))
	for _, key := range sortedKeys(b) {
		folded := foldKey(key)
		if other, ok := seen[folded]; ok {
			return caseVariantError(other, key, "")
		}
		seen[folded] = key
	}
	return nil
}

func caseVariantError(a, b, where string) error {
	return fmt.Errorf("%q and %q%s name the same field; keep one", a, b, where)
}

func sortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// rejectFieldCaseVariants refuses two keys in one object of b that decode
// into the same struct field of T. Only keys that fold alike are candidates.
// When there are any, b is decoded into T once and encoded again: a map keeps
// every key as given, while a struct field comes back once, under its own
// name. So a group of keys that does not all come back named a struct field,
// and its keys would compete for it.
func rejectFieldCaseVariants[T any](b Body) error {
	type caseGroup struct {
		path []any
		keys []string
	}
	var groups []caseGroup
	walkCaseGroups(map[string]any(b), nil, func(path []any, keys []string) {
		groups = append(groups, caseGroup{path: append([]any(nil), path...), keys: keys})
	})
	if len(groups) == 0 {
		return nil
	}
	encoded, ok := roundTrip[T](b)
	if !ok {
		return nil // the body does not decode; Decode reports why
	}
	for _, g := range groups {
		object, found := lookupPath(encoded, g.path)
		for _, key := range g.keys {
			if _, kept := object[key]; !found || !kept {
				return caseVariantError(g.keys[0], g.keys[1], pathText(g.path))
			}
		}
	}
	return nil
}

// walkCaseGroups calls fn for every object in v that holds two or more keys
// folding alike, outermost first. path is a stack shared by the calls: fn
// copies it to keep it.
func walkCaseGroups(v any, path []any, fn func(path []any, keys []string)) {
	switch node := v.(type) {
	case Body:
		walkCaseGroups(map[string]any(node), path, fn)
	case map[string]any:
		keys := sortedKeys(node)
		groups := make(map[string][]string, len(keys))
		for _, key := range keys {
			groups[foldKey(key)] = append(groups[foldKey(key)], key)
		}
		for _, key := range keys {
			if group := groups[foldKey(key)]; len(group) > 1 && group[0] == key {
				fn(path, group)
			}
		}
		for _, key := range keys {
			walkCaseGroups(node[key], append(path, key), fn)
		}
	case []any:
		for i, child := range node {
			walkCaseGroups(child, append(path, i), fn)
		}
	}
}

// roundTrip decodes b into T (refusing unknown fields) and encodes it again
// as a generic value; ok is false when b does not decode.
func roundTrip[T any](b Body) (any, bool) {
	raw, err := json.Marshal(b)
	if err != nil {
		return nil, false
	}
	var typed T
	strict := json.NewDecoder(bytes.NewReader(raw))
	strict.DisallowUnknownFields()
	if err := strict.Decode(&typed); err != nil {
		return nil, false
	}
	again, err := json.Marshal(typed)
	if err != nil {
		return nil, false
	}
	dec := json.NewDecoder(bytes.NewReader(again))
	dec.UseNumber()
	var generic any
	if err := dec.Decode(&generic); err != nil {
		return nil, false
	}
	return generic, true
}

// lookupPath finds the object at path in an encoded value; an object key on
// the path matches exactly, else by folding (a struct field comes back under
// its own name).
func lookupPath(v any, path []any) (map[string]any, bool) {
	for _, step := range path {
		switch step := step.(type) {
		case string:
			object, ok := v.(map[string]any)
			if !ok {
				return nil, false
			}
			child, ok := object[step]
			if !ok {
				for k, c := range object {
					if foldKey(k) == foldKey(step) {
						child, ok = c, true
						break
					}
				}
			}
			if !ok {
				return nil, false
			}
			v = child
		case int:
			items, ok := v.([]any)
			if !ok || step >= len(items) {
				return nil, false
			}
			v = items[step]
		}
	}
	object, ok := v.(map[string]any)
	return object, ok
}

func pathText(path []any) string {
	if len(path) == 0 {
		return ""
	}
	parts := make([]string, 0, len(path))
	for _, step := range path {
		parts = append(parts, fmt.Sprint(step))
	}
	return fmt.Sprintf(" (in %q)", strings.Join(parts, "."))
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
	if err := rejectFieldCaseVariants[T](b); err != nil {
		return out, cerr.Usage("invalid request body: %v", err)
	}
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
