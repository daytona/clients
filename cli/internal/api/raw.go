// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package api

import (
	"bytes"
	"encoding/json"
)

// Raw is a decoded API value that keeps the JSON it was decoded from. The CLI
// reads the typed value V and prints JSON (the API's representation): fields a
// newer API adds pass through, and fields the API left out stay absent instead
// of turning into zero values.
type Raw[T any] struct {
	V    T
	JSON json.RawMessage
}

// UnmarshalJSON keeps a copy of data and decodes it into V.
func (r *Raw[T]) UnmarshalJSON(data []byte) error {
	r.JSON = append(json.RawMessage(nil), data...)
	return json.Unmarshal(data, &r.V)
}

// MarshalJSON returns the JSON the value was decoded from, or V's encoding
// for a value the CLI built itself.
func (r Raw[T]) MarshalJSON() ([]byte, error) {
	if len(bytes.TrimSpace(r.JSON)) > 0 {
		return r.JSON, nil
	}
	return json.Marshal(r.V)
}

// Values returns the decoded values of rs.
func Values[T any](rs []Raw[T]) []T {
	out := make([]T, len(rs))
	for i, r := range rs {
		out[i] = r.V
	}
	return out
}

// DecodeRaw decodes data into a Raw.
func DecodeRaw[T any](data []byte) (Raw[T], error) {
	var r Raw[T]
	err := json.Unmarshal(data, &r)
	return r, err
}

// Page is one page of a list response: the items under the response's
// collection field, and its continuation ("" on the last page).
type Page[T any] struct {
	Items      []Raw[T]
	NextCursor string
}

// DecodePage reads the items under field and next_cursor from a list
// response body. An empty body is an empty page.
func DecodePage[T any](body []byte, field string) (Page[T], error) {
	page := Page[T]{Items: []Raw[T]{}}
	if len(bytes.TrimSpace(body)) == 0 {
		return page, nil
	}
	var envelope map[string]json.RawMessage
	if err := json.Unmarshal(body, &envelope); err != nil {
		return Page[T]{}, err
	}
	if items, ok := envelope[field]; ok && string(bytes.TrimSpace(items)) != "null" {
		if err := json.Unmarshal(items, &page.Items); err != nil {
			return Page[T]{}, err
		}
	}
	if next, ok := envelope["next_cursor"]; ok && string(bytes.TrimSpace(next)) != "null" {
		if err := json.Unmarshal(next, &page.NextCursor); err != nil {
			return Page[T]{}, err
		}
	}
	return page, nil
}
