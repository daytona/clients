// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package output

import (
	"bytes"
	"encoding/json"
	"strings"
	"unicode"
)

// SnakeCase renders v as the CLI's machine output for a value whose wire
// form uses camelCase keys (the toolbox API): the same document with every
// object key in snake_case (`exitCode` → `exit_code`, `nextCursor` →
// `next_cursor`). Values are untouched. Use it only on types without
// caller-chosen map keys.
func SnakeCase(v any) (any, error) {
	raw, err := json.Marshal(v)
	if err != nil {
		return nil, err
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var generic any
	if err := dec.Decode(&generic); err != nil {
		return nil, err
	}
	return snakeKeys(generic), nil
}

func snakeKeys(v any) any {
	switch node := v.(type) {
	case map[string]any:
		out := make(map[string]any, len(node))
		for k, child := range node {
			out[SnakeCaseKey(k)] = snakeKeys(child)
		}
		return out
	case []any:
		for i, child := range node {
			node[i] = snakeKeys(child)
		}
		return node
	default:
		return v
	}
}

// SnakeCaseKey converts one camelCase key: an upper-case letter starts a new
// word, and a run of capitals is one word (`processID` → `process_id`).
func SnakeCaseKey(key string) string {
	runes := []rune(key)
	var b strings.Builder
	for i, r := range runes {
		if unicode.IsUpper(r) {
			prevLower := i > 0 && (unicode.IsLower(runes[i-1]) || unicode.IsDigit(runes[i-1]))
			nextLower := i+1 < len(runes) && unicode.IsLower(runes[i+1])
			prevUpper := i > 0 && unicode.IsUpper(runes[i-1])
			if i > 0 && runes[i-1] != '_' && (prevLower || (prevUpper && nextLower)) {
				b.WriteByte('_')
			}
			b.WriteRune(unicode.ToLower(r))
			continue
		}
		b.WriteRune(r)
	}
	return b.String()
}
