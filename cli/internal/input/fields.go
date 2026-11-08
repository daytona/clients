// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package input

import (
	"fmt"
	"sort"
	"strings"

	"github.com/daytona/clients/cli/internal/cerr"
)

// Fields is the shape of the JSON a request type accepts: an object's exact
// field names, a map's values (its keys are the caller's own), a list's
// items. A nil *Fields accepts any value.
type Fields struct {
	Object map[string]*Fields
	Map    *Fields
	List   *Fields
	// IsMap and IsList tell a map or list of free values from a leaf.
	IsMap, IsList bool
}

// CheckKeys refuses a key that is not exactly the name of a request field,
// at any depth (keys of map values are the caller's own and are not
// checked). The API matches field names exactly; a key that differs only in
// case would otherwise be read as the field by the CLI and refused by
// nothing.
func CheckKeys(f *Fields, v any) error {
	var path []string
	return checkKeys(f, v, &path)
}

func checkKeys(f *Fields, v any, path *[]string) error {
	if f == nil {
		return nil
	}
	switch {
	case f.Object != nil:
		object, ok := asObject(v)
		if !ok {
			return nil // a type mismatch; decoding reports it
		}
		for _, key := range sortedKeys(object) {
			child, known := f.Object[key]
			if !known {
				return unknownKey(f, key, *path)
			}
			*path = append(*path, key)
			err := checkKeys(child, object[key], path)
			*path = (*path)[:len(*path)-1]
			if err != nil {
				return err
			}
		}
	case f.IsMap:
		object, ok := asObject(v)
		if !ok {
			return nil
		}
		for _, key := range sortedKeys(object) {
			*path = append(*path, key)
			err := checkKeys(f.Map, object[key], path)
			*path = (*path)[:len(*path)-1]
			if err != nil {
				return err
			}
		}
	case f.IsList:
		items, ok := v.([]any)
		if !ok {
			return nil
		}
		for i, item := range items {
			*path = append(*path, fmt.Sprint(i))
			err := checkKeys(f.List, item, path)
			*path = (*path)[:len(*path)-1]
			if err != nil {
				return err
			}
		}
	}
	return nil
}

func unknownKey(f *Fields, key string, path []string) error {
	where := ""
	if len(path) > 0 {
		where = fmt.Sprintf(" (in %q)", strings.Join(path, "."))
	}
	e := cerr.Usage("invalid request body: unknown field %q%s", key, where)
	for name := range f.Object {
		if strings.EqualFold(name, key) {
			return e.WithHint("field names are exact: did you mean %q?", name)
		}
	}
	return e
}

func asObject(v any) (map[string]any, bool) {
	switch node := v.(type) {
	case Body:
		return node, true
	case map[string]any:
		return node, true
	}
	return nil, false
}

func sortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
