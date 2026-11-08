// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package input

import (
	"encoding"
	"encoding/json"
	"flag"
	"fmt"
	"go/format"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	gen "github.com/daytona/clients/cli/gen/api"
)

var updateFields = flag.Bool("update", false, "rewrite request_fields_gen.go")

// decodedRequests are the request types --file bodies decode into
// (input.Decode / workload.DecodeBody); a missing one fails Decode at run
// time and every --file command's tests with it.
var decodedRequests = []any{
	gen.CreateImageRequest{},
	gen.CreateMacosSandboxRequest{},
	gen.CreateReservationRequest{},
	gen.CreateReservationsBulkRequest{},
	gen.CreateSandboxFromSnapshotRequest{},
	gen.CreateSandboxImageRequest{},
	gen.CreateSandboxRequest{},
	gen.CreateSandboxSnapshotRequest{},
	gen.CreateVolumeRequest{},
	gen.SetSandboxLabelsRequest{},
	gen.UpdateSandboxLifecyclePolicyRequest{},
}

var (
	jsonUnmarshaler = reflect.TypeFor[json.Unmarshaler]()
	textUnmarshaler = reflect.TypeFor[encoding.TextUnmarshaler]()
)

// fieldsFailure reports a type the table cannot describe; tests set it to
// t.Fatalf so a new raw-JSON type never becomes an unchecked leaf.
var fieldsFailure = func(format string, a ...any) { panic(fmt.Sprintf(format, a...)) }

// fieldsOf builds the JSON shape encoding/json accepts for t.
func fieldsOf(t reflect.Type, depth int) *Fields {
	for t.Kind() == reflect.Pointer {
		t = t.Elem()
	}
	if depth > 20 {
		return nil
	}
	if reflect.PointerTo(t).Implements(jsonUnmarshaler) || reflect.PointerTo(t).Implements(textUnmarshaler) {
		if t.PkgPath() != reflect.TypeFor[gen.CreateImageRequest]().PkgPath() {
			return nil // a scalar such as a time, a date or a UUID
		}
		if isUnion(t) {
			return unionFields(t, depth)
		}
		fieldsFailure("%s decodes its own JSON and is not a union the table can expand: teach fieldsOf its shape", t)
		return nil
	}
	switch t.Kind() {
	case reflect.Struct:
		f := &Fields{Object: map[string]*Fields{}}
		collectFields(t, f.Object, depth)
		return f
	case reflect.Map:
		return &Fields{IsMap: true, Map: fieldsOf(t.Elem(), depth+1)}
	case reflect.Slice, reflect.Array:
		if t.Elem().Kind() == reflect.Uint8 {
			return nil
		}
		return &Fields{IsList: true, List: fieldsOf(t.Elem(), depth+1)}
	}
	return nil
}

// isUnion reports a generated oneOf type: a struct holding only the raw
// `union` JSON.
func isUnion(t reflect.Type) bool {
	return t.Kind() == reflect.Struct && t.NumField() == 1 && t.Field(0).Name == "union" &&
		t.Field(0).Type == reflect.TypeFor[json.RawMessage]()
}

// unionFields describes a generated oneOf type: each member (the As<Name>N
// methods) by its own fields, selected by the member's discriminator value,
// which the contract's schema of the same name declares.
func unionFields(t reflect.Type, depth int) *Fields {
	schema := contractSchema(t.Name())
	oneOf, _ := schema["oneOf"].([]any)
	if len(oneOf) == 0 {
		fieldsFailure("%s: no oneOf schema %q in the contract", t, t.Name())
		return nil
	}
	u := &Union{Members: map[string]*Fields{}}
	for i, raw := range oneOf {
		member, ok := t.MethodByName(fmt.Sprintf("As%s%d", t.Name(), i))
		if !ok || member.Type.NumOut() != 2 {
			fieldsFailure("%s: no As%s%d member accessor", t, t.Name(), i)
			return nil
		}
		fields := fieldsOf(member.Type.Out(0), depth+1)
		memberSchema := resolveSchema(raw)
		properties, _ := memberSchema["properties"].(map[string]any)
		key, value := "", ""
		names := make([]string, 0, len(properties))
		for name, prop := range properties {
			names = append(names, name)
			p, _ := prop.(map[string]any)
			if p["x-daytona-exact-enum"] == "discriminator" {
				enum, _ := p["enum"].([]any)
				if len(enum) != 1 {
					fieldsFailure("%s member %d: discriminator %q needs one value", t, i, name)
					return nil
				}
				key, value = name, fmt.Sprint(enum[0])
			}
		}
		if key == "" || (u.Key != "" && u.Key != key) {
			fieldsFailure("%s member %d: no single discriminator property", t, i)
			return nil
		}
		got := make([]string, 0, len(fields.Object))
		for name := range fields.Object {
			got = append(got, name)
		}
		sort.Strings(names)
		sort.Strings(got)
		if strings.Join(names, ",") != strings.Join(got, ",") {
			fieldsFailure("%s member %d: Go fields %v, contract properties %v", t, i, got, names)
			return nil
		}
		u.Key = key
		u.Members[value] = fields
	}
	return &Fields{Union: u}
}

var contractSchemas map[string]any

func contractSchema(name string) map[string]any {
	if contractSchemas == nil {
		raw, err := os.ReadFile(filepath.Join("..", "..", "..", "contract", "api-openapi.json"))
		if err != nil {
			fieldsFailure("read the contract: %v", err)
			return nil
		}
		var doc struct {
			Components struct {
				Schemas map[string]any `json:"schemas"`
			} `json:"components"`
		}
		if err := json.Unmarshal(raw, &doc); err != nil {
			fieldsFailure("parse the contract: %v", err)
			return nil
		}
		contractSchemas = doc.Components.Schemas
	}
	schema, _ := contractSchemas[name].(map[string]any)
	return schema
}

func resolveSchema(raw any) map[string]any {
	schema, _ := raw.(map[string]any)
	if ref, ok := schema["$ref"].(string); ok {
		return contractSchema(strings.TrimPrefix(ref, "#/components/schemas/"))
	}
	return schema
}

func collectFields(t reflect.Type, out map[string]*Fields, depth int) {
	for i := range t.NumField() {
		f := t.Field(i)
		tag := f.Tag.Get("json")
		if tag == "-" {
			continue
		}
		name, _, _ := strings.Cut(tag, ",")
		if f.Anonymous && name == "" {
			ft := f.Type
			if ft.Kind() == reflect.Pointer {
				ft = ft.Elem()
			}
			if ft.Kind() == reflect.Struct {
				collectFields(ft, out, depth)
				continue
			}
		}
		if !f.IsExported() {
			continue
		}
		if name == "" {
			name = f.Name
		}
		out[name] = fieldsOf(f.Type, depth+1)
	}
}

// fieldsLiteral renders f as Go source.
func fieldsLiteral(f *Fields) string {
	switch {
	case f == nil:
		return "nil"
	case f.Object != nil:
		names := make([]string, 0, len(f.Object))
		for name := range f.Object {
			names = append(names, name)
		}
		sort.Strings(names)
		var b strings.Builder
		b.WriteString("&Fields{Object: map[string]*Fields{\n")
		for _, name := range names {
			fmt.Fprintf(&b, "%q: %s,\n", name, fieldsLiteral(f.Object[name]))
		}
		b.WriteString("}}")
		return b.String()
	case f.IsMap:
		return "&Fields{IsMap: true, Map: " + fieldsLiteral(f.Map) + "}"
	case f.IsList:
		return "&Fields{IsList: true, List: " + fieldsLiteral(f.List) + "}"
	case f.Union != nil:
		values := make([]string, 0, len(f.Union.Members))
		for value := range f.Union.Members {
			values = append(values, value)
		}
		sort.Strings(values)
		var b strings.Builder
		fmt.Fprintf(&b, "&Fields{Union: &Union{Key: %q, Members: map[string]*Fields{\n", f.Union.Key)
		for _, value := range values {
			fmt.Fprintf(&b, "%q: %s,\n", value, fieldsLiteral(f.Union.Members[value]))
		}
		b.WriteString("}}}")
		return b.String()
	}
	return "nil"
}

// registerFields adds a test type's table, as the generated file does for
// the request types.
func registerFields[T any](t *testing.T) {
	t.Helper()
	var v T
	key := fmt.Sprintf("%T", v)
	requestFields[key] = fieldsOf(reflect.TypeFor[T](), 0)
	t.Cleanup(func() { delete(requestFields, key) })
}

func renderRequestFields(t *testing.T) []byte {
	t.Helper()
	fieldsFailure = t.Fatalf
	var b strings.Builder
	b.WriteString("// Copyright Daytona Platforms Inc.\n// SPDX-License-Identifier: AGPL-3.0\n\n")
	b.WriteString("// Code generated by `go test ./internal/input -run TestRequestFieldsAreCurrent -update`. DO NOT EDIT.\n\n")
	b.WriteString("package input\n\n")
	b.WriteString("// requestFields holds the exact JSON field names of every request type a\n// --file body decodes into, keyed by the type's %T name.\n")
	b.WriteString("var requestFields = map[string]*Fields{\n")
	for _, v := range decodedRequests {
		fmt.Fprintf(&b, "%q: %s,\n", fmt.Sprintf("%T", v), fieldsLiteral(fieldsOf(reflect.TypeOf(v), 0)))
	}
	b.WriteString("}\n")
	src, err := format.Source([]byte(b.String()))
	if err != nil {
		t.Fatal(err)
	}
	return src
}

// The committed table must match the generated request types.
func TestRequestFieldsAreCurrent(t *testing.T) {
	want := renderRequestFields(t)
	if *updateFields {
		if err := os.WriteFile("request_fields_gen.go", want, 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	got, err := os.ReadFile("request_fields_gen.go")
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != string(want) {
		t.Fatal("request_fields_gen.go is stale: run go test ./internal/input -run TestRequestFieldsAreCurrent -update")
	}
}

// Every union of the generated API types expands to its members, so a new
// oneOf in a request becomes checked, not an unchecked leaf.
func TestEveryGeneratedUnionExpands(t *testing.T) {
	fieldsFailure = t.Fatalf
	for _, v := range []any{gen.PublicImageSource{}, gen.ImageSourceValue{}} {
		f := fieldsOf(reflect.TypeOf(v), 0)
		if f == nil || f.Union == nil || len(f.Union.Members) < 2 {
			t.Fatalf("%T: %+v", v, f)
		}
	}
}
