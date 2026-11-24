// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

// Package input merges a --file JSON body with explicit flags: the
// file is the base, flags the caller marked as changed override it
// field by field. `--file -` reads stdin.
package input

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"os"
	"sort"
	"strconv"
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
	var v any
	dec := json.NewDecoder(r)
	dec.UseNumber()
	if err := dec.Decode(&v); err != nil {
		return nil, cerr.Usage("--file %s: invalid JSON: %v", path, err)
	}
	b, ok := v.(map[string]any)
	if !ok {
		return nil, cerr.Usage("--file %s must contain a JSON object, got %s", path, jsonKind(v))
	}
	if err := dec.Decode(new(json.RawMessage)); err != io.EOF {
		return nil, cerr.Usage("--file %s: trailing data after the JSON object", path)
	}
	return Body(b), nil
}

// jsonKind names the JSON type of a decoded value, for messages.
func jsonKind(v any) string {
	switch v.(type) {
	case nil:
		return "null"
	case []any:
		return "an array"
	case string:
		return "a string"
	case bool:
		return "a boolean"
	case json.Number, float64:
		return "a number"
	case map[string]any:
		return "an object"
	}
	return fmt.Sprintf("%T", v)
}

// Set writes a field unconditionally.
func (b Body) Set(key string, v any) {
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

// RejectNulls refuses an explicit null for any of fields: the API
// distinguishes null from omitted where these are used.
func RejectNulls(b Body, fields ...string) error {
	for _, field := range fields {
		if value, ok := b[field]; ok && value == nil {
			return cerr.Usage("%q is not nullable; omit it to keep the current value", field)
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

// CheckKeysFor checks b's keys against request type T's field names
// (CheckKeys); Decode runs it too.
func CheckKeysFor[T any](b Body) error {
	var v T
	fields, ok := requestFields[fmt.Sprintf("%T", v)]
	if !ok {
		return cerr.New(cerr.ExitFailure, "no field table for %T (regenerate request_fields_gen.go)", v)
	}
	return CheckKeys(fields, map[string]any(b))
}

// Decode marshals the merged body into a typed request struct, so the server's
// schema (via the generated types) validates what the flags and --file
// produced. Explicit JSON nulls are not representable once a generated
// `omitempty` pointer field is materialized into the typed request, so callers
// must reject them before Decode when the API distinguishes null from omitted.
// Keys must name request fields exactly (CheckKeys); encoding/json alone
// would also accept a key that differs only in case.
// One implementation for every command family.
func Decode[T any](b Body) (T, error) {
	var out T
	if err := CheckKeysFor[T](b); err != nil {
		return out, err
	}
	raw, err := json.Marshal(b)
	if err != nil {
		return out, fmt.Errorf("encode request body: %w", err)
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&out); err != nil {
		var typeErr *json.UnmarshalTypeError
		if errors.As(err, &typeErr) && typeErr.Field != "" {
			if problem := numberProblem(fmt.Sprint(typeErr.Type), typeErr.Value); problem != "" {
				return out, cerr.Usage("invalid request body: field %q %s", typeErr.Field, problem)
			}
			return out, cerr.Usage("invalid request body: field %q cannot be %s", elementPath(b, typeErr.Field, typeErr.Value), jsonValueKind(typeErr.Value))
		}
		return out, cerr.Usage("invalid request body: %v", err)
	}
	return out, nil
}

// elementPath names the value encoding/json refused more precisely when it
// can: encoding/json names a map or array field ("env") for a wrong value
// inside it, so when field holds a map or an array of scalars in b, the
// first entry of the refused JSON kind is named ("env.A", "ports[1]").
// Otherwise field is returned as given.
func elementPath(b Body, field, value string) string {
	var current any = map[string]any(b)
	for _, segment := range strings.Split(field, ".") {
		object, ok := current.(map[string]any)
		if !ok {
			return field
		}
		current = object[segment]
	}
	kind, _, _ := strings.Cut(value, " ")
	switch container := current.(type) {
	case map[string]any:
		keys := make([]string, 0, len(container))
		for key := range container {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			if jsonKind(container[key]) == jsonValueKind(kind) {
				return field + "." + key
			}
		}
	case []any:
		for i, item := range container {
			if jsonKind(item) == jsonValueKind(kind) {
				return fmt.Sprintf("%s[%d]", field, i)
			}
		}
	}
	return field
}

// integerRanges are the bounds of the integer types request fields use.
var integerRanges = map[string][2]string{
	"int":    {"-9223372036854775808", "9223372036854775807"},
	"int8":   {"-128", "127"},
	"int16":  {"-32768", "32767"},
	"int32":  {"-2147483648", "2147483647"},
	"int64":  {"-9223372036854775808", "9223372036854775807"},
	"uint":   {"0", "18446744073709551615"},
	"uint8":  {"0", "255"},
	"uint16": {"0", "65535"},
	"uint32": {"0", "4294967295"},
	"uint64": {"0", "18446744073709551615"},
}

// numberProblem says what is wrong with a JSON number encoding/json could not
// store in a numeric field of goType ("int64", "*int32", "float64"), or ""
// when the value is not a number or the field is not numeric, which the
// generic "cannot be <kind>" message covers.
func numberProblem(goType, value string) string {
	number, ok := strings.CutPrefix(value, "number ")
	if !ok {
		return ""
	}
	goType = strings.TrimLeft(goType, "*")
	if goType == "float32" || goType == "float64" {
		return fmt.Sprintf("is out of range for a number, got %s", number)
	}
	bounds, ok := integerRanges[goType]
	if !ok {
		return ""
	}
	parsed, _, err := big.ParseFloat(number, 10, 256, big.ToNearestEven)
	if errors.Is(err, strconv.ErrRange) || (err == nil && parsed.IsInf()) {
		return fmt.Sprintf("is out of range (%s to %s), got %s", bounds[0], bounds[1], number)
	}
	if err != nil || !parsed.IsInt() {
		return fmt.Sprintf("must be a whole number, got %s", number)
	}
	n, _ := parsed.Int(nil)
	lo, _ := new(big.Int).SetString(bounds[0], 10)
	hi, _ := new(big.Int).SetString(bounds[1], 10)
	if n.Cmp(lo) < 0 || n.Cmp(hi) > 0 {
		return fmt.Sprintf("is out of range (%s to %s), got %s", bounds[0], bounds[1], number)
	}
	return fmt.Sprintf("must be written as a whole number without a fraction or exponent, got %s", number)
}

// jsonValueKind names the JSON value encoding/json reports in
// UnmarshalTypeError.Value ("string", "number 1.5", "array", ...).
func jsonValueKind(value string) string {
	kind, _, _ := strings.Cut(value, " ")
	switch kind {
	case "array", "object":
		return "an " + kind
	case "bool":
		return "a boolean"
	case "":
		return "another type"
	}
	return "a " + kind
}
