// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"fmt"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

// checkRequestUTF8 is the shared API/toolbox JSON boundary. encoding/json
// replaces invalid string bytes rather than reporting them. Inspect the typed
// body before authentication or encoding can send a different caller value.
func checkRequestUTF8(body any) error {
	return requestUTF8Field(reflect.ValueOf(body), "", 0)
}

func requestUTF8Field(value reflect.Value, path string, depth int) error {
	// Cyclic bodies are invalid JSON. Bound traversal so they cannot overflow
	// the stack before encoding/json gets a chance to reject them.
	if depth > 1000 {
		return badRequest("request body is cyclic or nested too deeply")
	}
	switch value.Kind() {
	case reflect.String:
		if !utf8.ValidString(value.String()) {
			return badRequest("request field " + path + " must be valid UTF-8")
		}
	case reflect.Pointer, reflect.Interface:
		if !value.IsNil() {
			return requestUTF8Field(value.Elem(), path, depth+1)
		}
	case reflect.Struct:
		return requestUTF8Struct(value, path, depth+1)
	case reflect.Map:
		return requestUTF8Map(value, path, depth+1)
	case reflect.Slice, reflect.Array:
		// []byte (including json.RawMessage/JSONValue) is encoded as bytes or
		// already-encoded JSON, never as a string that json.Marshal rewrites.
		if value.Kind() == reflect.Slice && value.Type().Elem().Kind() == reflect.Uint8 {
			return nil
		}
		for index := range value.Len() {
			fieldPath := path + "[" + strconv.Itoa(index) + "]"
			if err := requestUTF8Field(value.Index(index), fieldPath, depth+1); err != nil {
				return err
			}
		}
	case reflect.Invalid, reflect.Bool,
		reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64,
		reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Uintptr,
		reflect.Float32, reflect.Float64, reflect.Complex64, reflect.Complex128,
		reflect.Chan, reflect.Func, reflect.UnsafePointer:
		// Other kinds cannot contain a JSON string; json.Marshal handles
		// unsupported values and numeric constraints.
	}
	return nil
}

func requestUTF8Struct(value reflect.Value, path string, depth int) error {
	for index := range value.NumField() {
		field := value.Type().Field(index)
		if !field.IsExported() {
			continue
		}
		name, _, _ := strings.Cut(field.Tag.Get("json"), ",")
		if name == "-" {
			continue
		}
		fieldPath := path
		if !field.Anonymous || name != "" {
			if name == "" {
				name = field.Name
			}
			if fieldPath != "" {
				fieldPath += "."
			}
			fieldPath += name
		}
		if err := requestUTF8Field(value.Field(index), fieldPath, depth); err != nil {
			return err
		}
	}
	return nil
}

func requestUTF8Map(value reflect.Value, path string, depth int) error {
	keys := value.MapKeys()
	// JSON object string keys are sorted by encoding/json. Match that order
	// so multiple malformed fields always report the same first path.
	if value.Type().Key().Kind() == reflect.String {
		sort.Slice(keys, func(i, j int) bool { return keys[i].String() < keys[j].String() })
	}
	for _, key := range keys {
		keyPath := path + "[" + fmt.Sprint(key) + "]"
		if key.Kind() == reflect.String {
			keyPath = path + "[" + strconv.Quote(key.String()) + "]"
		}
		if err := requestUTF8Field(key, keyPath, depth); err != nil {
			return err
		}
		if err := requestUTF8Field(value.MapIndex(key), keyPath, depth); err != nil {
			return err
		}
	}
	return nil
}
