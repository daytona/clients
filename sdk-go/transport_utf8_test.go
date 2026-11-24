// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestRequestUTF8_whenNestedStringIsInvalid_reportsJSONPath(t *testing.T) {
	type entry struct {
		Text string `json:"text"`
	}
	type body struct {
		Rows map[string][1]*entry `json:"rows"`
	}
	// Given: interfaces, struct fields, maps, arrays and pointers.
	input := &body{Rows: map[string][1]*entry{"a": {{Text: "bad\xff"}}}}
	// When
	err := checkRequestUTF8(input)
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	require.ErrorContains(t, err, `rows["a"][0].text`)
}

func TestRequestUTF8_whenStringsAreValid_acceptsJSONValues(t *testing.T) {
	// Given
	type embedded struct{ Text string }
	input := struct {
		embedded
		Rows    []any           `json:"rows"`
		Bytes   []byte          `json:"bytes"`
		Raw     json.RawMessage `json:"raw"`
		Ignored string          `json:"-"`
		hidden  string
	}{
		embedded: embedded{Text: "世界"}, Rows: []any{nil, "valid", map[string]string{"😀": "ü"}},
		Bytes: []byte{0xff}, Raw: json.RawMessage(`{"ok":true}`), Ignored: "\xff", hidden: "\xff",
	}
	// When
	err := checkRequestUTF8(input)
	// Then
	require.NoError(t, err)
}

func TestRequestUTF8_whenBodyCycles_returnsBadRequest(t *testing.T) {
	// Given
	input := map[string]any{}
	input["self"] = input
	// When
	err := checkRequestUTF8(input)
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
}

func TestRequestUTF8_whenMultipleMapStringsAreInvalid_reportsFirstKey(t *testing.T) {
	// Given
	input := map[string]string{"z": "\xff", "a": "\xfe"}
	// When
	err := checkRequestUTF8(input)
	// Then
	require.ErrorContains(t, err, `["a"]`)
}
