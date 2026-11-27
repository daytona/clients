// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"maps"
	"net/http"
	"slices"
	"unicode/utf8"
)

// SetLabels replaces labels and returns the applied map. Nil clears all but the
// reserved name label. Unless name is supplied, a detail read preserves its
// current value; this read and write are not atomic. Empty keys return
// ErrBadRequest before I/O, as do keys or values that are not valid UTF-8.
// ErrConflict identifies deleting or deleted sandboxes.
func (s *Sandbox) SetLabels(ctx context.Context, labels map[string]string) (map[string]string, error) {
	if err := checkLabels(labels); err != nil {
		return nil, err
	}
	body := make(map[string]string, len(labels)+1)
	maps.Copy(body, labels)
	if _, exists := body["name"]; !exists {
		if err := s.Refresh(ctx); err != nil {
			return nil, err
		}
		if name, found := s.Info().Labels["name"]; found {
			body["name"] = name
		}
	}
	segment, err := pathSegment(s.id)
	if err != nil {
		return nil, err
	}
	var raw JSONValue
	if requestErr := s.client.api().do(ctx, apiRequest{
		Method: http.MethodPut, Path: "/sandboxes/" + segment + "/labels",
		Body: struct {
			Labels map[string]string `json:"labels"`
		}{body}, Result: &raw,
	}); requestErr != nil {
		return nil, requestErr
	}
	object, err := parseObject(raw, "sandbox labels")
	if err != nil {
		return nil, err
	}
	values, err := field[jsonObject](object, "labels", true)
	if err != nil {
		return nil, err
	}
	applied := make(map[string]string, len(values))
	for key := range values {
		value, fieldErr := field[string](values, key, true)
		if fieldErr != nil {
			return nil, badRequest("sandbox labels response has invalid labels." + key)
		}
		applied[key] = value
	}
	s.mu.Lock()
	s.info.Labels = maps.Clone(applied)
	s.mu.Unlock()
	return applied, nil
}

// checkLabels refuses labels the API cannot read, before any request: an empty
// key, then a key or a value that is not valid UTF-8, in key order.
func checkLabels(labels map[string]string) error {
	if _, exists := labels[""]; exists {
		return badRequest("label keys must not be empty")
	}
	for _, key := range slices.Sorted(maps.Keys(labels)) {
		if !utf8.ValidString(key) {
			return badRequest("label keys must be valid UTF-8")
		}
		if !utf8.ValidString(labels[key]) {
			return badRequest("label values must be valid UTF-8")
		}
	}
	return nil
}
