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
// which the contract's schema of the same name declares. An untagged union of
// scalars and a single object (a name, or an object that spells it out) is
// described by that object's fields: a scalar value is not an object, so
// CheckKeys passes it and decoding judges it.
func unionFields(t reflect.Type, depth int) *Fields {
	schema := contractSchema(t.Name())
	oneOf, _ := schema["oneOf"].([]any)
	if len(oneOf) == 0 {
		if member, ok := soleMember(t); ok {
			return fieldsOf(member, depth+1)
		}
		fieldsFailure("%s: no oneOf schema %q in the contract", t, t.Name())
		return nil
	}
	if f, ok := scalarOrObjectFields(t, oneOf, depth); ok {
		return f
	}
	u := &Union{Members: map[string]*Fields{}}
	for i, raw := range oneOf {
		fields, ok := unionMember(t, i, raw, depth)
		if !ok {
			return nil
		}
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
		if !sameFields(t, i, fields, names) {
			return nil
		}
		u.Key = key
		u.Members[value] = fields
	}
	return &Fields{Union: u}
}

// soleMember is the one member of an inline oneOf that wraps a single schema
// (a property's `oneOf: [$ref]`, generated as <Parent>_<Field> with one As<Schema>
// accessor): the property takes that schema's values as they are.
func soleMember(t reflect.Type) (reflect.Type, bool) {
	var member reflect.Type
	for i := range t.NumMethod() {
		m := t.Method(i)
		if !strings.HasPrefix(m.Name, "As") || m.Type.NumIn() != 1 || m.Type.NumOut() != 2 {
			continue
		}
		if member != nil {
			return nil, false
		}
		member = m.Type.Out(0)
	}
	return member, member != nil
}

// unionMember is the fields of member i of a generated oneOf type, read from
// its accessor: As<Name>i, or As<Schema> for a member that is a $ref.
func unionMember(t reflect.Type, i int, raw any, depth int) (*Fields, bool) {
	accessor := fmt.Sprintf("As%s%d", t.Name(), i)
	if m, _ := raw.(map[string]any); m != nil {
		if ref, ok := m["$ref"].(string); ok {
			accessor = "As" + strings.TrimPrefix(ref, "#/components/schemas/")
		}
	}
	member, ok := t.MethodByName(accessor)
	if !ok || member.Type.NumOut() != 2 {
		fieldsFailure("%s: no %s member accessor", t, accessor)
		return nil, false
	}
	return fieldsOf(member.Type.Out(0), depth+1), true
}

// scalarOrObjectFields describes an untagged oneOf whose members are scalars
// and exactly one object, by the object's fields. ok is false for any other
// oneOf.
func scalarOrObjectFields(t reflect.Type, oneOf []any, depth int) (*Fields, bool) {
	var object *Fields
	for i, raw := range oneOf {
		memberSchema := resolveSchema(raw)
		switch memberSchema["type"] {
		case "string", "integer", "number", "boolean":
			continue
		case "object":
			if object != nil {
				return nil, false
			}
			properties, _ := memberSchema["properties"].(map[string]any)
			names := make([]string, 0, len(properties))
			for name, prop := range properties {
				if p, _ := prop.(map[string]any); p["x-daytona-exact-enum"] == "discriminator" {
					return nil, false
				}
				names = append(names, name)
			}
			fields, ok := unionMember(t, i, raw, depth)
			if !ok || !sameFields(t, i, fields, names) {
				return nil, true
			}
			object = fields
		default:
			return nil, false
		}
	}
	return object, object != nil
}

// sameFields reports whether a member's Go fields are the contract's
// properties, failing the table when they are not.
func sameFields(t reflect.Type, i int, fields *Fields, names []string) bool {
	got := []string{}
	if fields != nil {
		for name := range fields.Object {
			got = append(got, name)
		}
	}
	sort.Strings(names)
	sort.Strings(got)
	if strings.Join(names, ",") != strings.Join(got, ",") {
		fieldsFailure("%s member %d: Go fields %v, contract properties %v", t, i, got, names)
		return false
	}
	return true
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
	failFieldsWith(t)
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
	failFieldsWith(t)
	for _, v := range []any{gen.PublicImageSource{}, gen.ImageSourceValue{}} {
		f := fieldsOf(reflect.TypeOf(v), 0)
		if f == nil || f.Union == nil || len(f.Union.Members) < 2 {
			t.Fatalf("%T: %+v", v, f)
		}
	}
}

// failFieldsWith makes fieldsOf fail t for the rest of the test.
func failFieldsWith(t *testing.T) {
	t.Helper()
	previous := fieldsFailure
	fieldsFailure = t.Fatalf
	t.Cleanup(func() { fieldsFailure = previous })
}

// A secret attachment is a name or an object that names its variable: names
// pass as they are, an object's keys are exact.
func TestDecode_secretAttachmentsTakeNamesAndObjects(t *testing.T) {
	b := Body{"image": "base", "secrets": []any{"API_KEY", map[string]any{"name": "tenant-key", "env": "OPENAI_API_KEY"}}}
	req, err := Decode[gen.CreateSandboxRequest](b)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(req.Secrets)
	if err != nil || string(raw) != `["API_KEY",{"env":"OPENAI_API_KEY","name":"tenant-key"}]` {
		t.Fatalf("secrets %s, %v", raw, err)
	}
	b = Body{"image": "base", "secrets": []any{map[string]any{"name": "tenant-key", "Env": "X"}}}
	if _, err := Decode[gen.CreateSandboxRequest](b); err == nil || !strings.Contains(err.Error(), `unknown field "Env" (in "secrets.0")`) {
		t.Fatalf("Decode: %v", err)
	}
}
