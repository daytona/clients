// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package input

import (
	"reflect"
	"strings"
)

// inlineUnionFields expands a generated Parent_Property wrapper only when
// the contract declares one referenced member with a matching Go accessor.
func inlineUnionFields(t reflect.Type, depth int) *Fields {
	parent, field, _ := strings.Cut(t.Name(), "_")
	properties, _ := contractSchema(parent)["properties"].(map[string]any)
	for name, raw := range properties {
		if !strings.EqualFold(name, field) {
			continue
		}
		schema := resolveSchema(raw)
		oneOf, _ := schema["oneOf"].([]any)
		if len(oneOf) != 1 {
			break
		}
		memberSchema, _ := oneOf[0].(map[string]any)
		ref, _ := memberSchema["$ref"].(string)
		memberName, ok := strings.CutPrefix(ref, "#/components/schemas/")
		if !ok || contractSchema(memberName) == nil {
			break
		}
		member, ok := t.MethodByName("As" + memberName)
		if !ok || member.Type.NumOut() != 2 || member.Type.Out(0).Name() != memberName {
			break
		}
		return fieldsOf(member.Type.Out(0), depth+1)
	}
	fieldsFailure("%s: no single-reference inline oneOf schema in the contract", t)
	return nil
}
