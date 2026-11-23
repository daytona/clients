// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"encoding/json"
	"math/big"
	"strconv"
	"strings"
	"unicode/utf8"
)

// JSONValue holds an arbitrary JSON value as encoded JSON, including objects,
// arrays, scalars and null. Use encoding/json to construct or decode it.
type JSONValue = json.RawMessage

const jsonMaxDepth = 600

func isJSONMediaType(value string) bool {
	media, _, _ := strings.Cut(value, ";")
	return strings.EqualFold(strings.TrimSpace(media), "application/json")
}

// checkJSON bounds nesting before encoding/json allocates a decoded value.
func checkJSON(data []byte) error {
	if !utf8.Valid(data) || !json.Valid(data) {
		return badRequest("expected JSON response: the body is not valid JSON")
	}
	depth, quoted, escaped := 0, false, false
	for _, char := range data {
		if quoted {
			switch {
			case escaped:
				escaped = false
			case char == '\\':
				escaped = true
			case char == '"':
				quoted = false
			}
			continue
		}
		switch char {
		case '"':
			quoted = true
		case '{', '[':
			depth++
			if depth > jsonMaxDepth {
				return badRequest("expected JSON response: the JSON is nested too deeply")
			}
		case '}', ']':
			depth--
		}
	}
	return nil
}

// jsonObject is confined to parsing boundaries. Domain models never retain it.
type jsonObject map[string]json.RawMessage

func parseObject(data []byte, description string) (jsonObject, error) {
	var result jsonObject
	if err := json.Unmarshal(data, &result); err != nil || result == nil {
		return nil, badRequest("expected " + description + " response object")
	}
	return result, nil
}

func present(raw json.RawMessage) bool {
	return len(raw) != 0 && !bytes.Equal(bytes.TrimSpace(raw), []byte("null"))
}

func field[T any](object jsonObject, name string, required bool) (T, error) {
	var value T
	raw := object[name]
	if !present(raw) {
		if required {
			return value, badRequest("response field " + name + " is required")
		}
		return value, nil
	}
	if err := json.Unmarshal(raw, &value); err != nil {
		return value, badRequest("response field " + name + " has an invalid type")
	}
	return value, nil
}

func integerField(object jsonObject, name string, required bool) (int64, error) {
	number, err := field[float64](object, name, required)
	if err != nil {
		return 0, err
	}
	if !present(object[name]) {
		return 0, nil
	}
	raw := strings.TrimSpace(string(object[name]))
	// Preserve exact integer literals; accept 3.0 as TypeScript does too.
	if integer, parseErr := strconv.ParseInt(raw, 10, 64); parseErr == nil {
		return integer, nil
	}
	// A zero double may be an underflowed fraction. Handle it without allocating
	// a rational denominator proportional to an untrusted exponent.
	if number == 0 {
		mantissa, _, _ := strings.Cut(strings.ToLower(raw), "e")
		if strings.Trim(mantissa, "-.0") == "" {
			return 0, nil
		}
		return 0, badRequest("response field " + name + " must be an int64")
	}
	exact, ok := new(big.Rat).SetString(raw)
	if !ok || !exact.IsInt() || !exact.Num().IsInt64() {
		return 0, badRequest("response field " + name + " must be an int64")
	}
	return exact.Num().Int64(), nil
}

func optionalErrorString(object jsonObject, name string) string {
	value, err := field[string](object, name, false)
	if err != nil {
		return ""
	}
	return value
}

func optionalErrorInteger(object jsonObject, name string) *int64 {
	if !present(object[name]) {
		return nil
	}
	value, err := integerField(object, name, false)
	if err != nil {
		return nil
	}
	return &value
}
