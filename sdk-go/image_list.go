// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"net/url"
	"sync/atomic"
	"unicode/utf8"
)

type artifactListReader struct {
	client   *Client
	path     string
	fallback *atomic.Bool
}

func (r artifactListReader) read(ctx context.Context, query url.Values) (JSONValue, error) {
	var raw JSONValue
	read := func(path string) error {
		return r.client.api().do(ctx, apiRequest{Path: path, Query: query, Result: &raw})
	}
	if r.fallback.Load() {
		err := read(r.path)
		var detail *Error
		if errors.As(err, &detail) && detail.StatusCode == 410 && detail.Code == CodeGone {
			r.fallback.Store(false)
			err = read(r.path + "/search")
		}
		return raw, err
	}
	err := read(r.path + "/search")
	if errorStatus(err) == 404 || (errorStatus(err) == 501 && errors.Is(err, ErrSearchNotConfigured)) {
		r.fallback.Store(true)
		err = read(r.path)
	}
	return raw, err
}

func artifactFilters(query url.Values, name, sandboxID string) error {
	for _, filter := range []struct{ key, value string }{
		{"name", name},
		{"sandbox_id", sandboxID},
	} {
		if !utf8.ValidString(filter.value) {
			return badRequest(filter.key + " must be valid UTF-8")
		}
		if filter.value != "" {
			query.Set(filter.key, filter.value)
		}
	}
	return nil
}

func artifactPage[T any](raw JSONValue, key string, parse func(JSONValue) (T, error)) (*Page[T], error) {
	object, err := parseObject(raw, key+" list")
	if err != nil {
		return nil, err
	}
	items, err := field[[]JSONValue](object, key, true)
	if err != nil {
		return nil, err
	}
	cursor, err := field[string](object, "next_cursor", false)
	if err != nil {
		return nil, err
	}
	result := &Page[T]{Items: make([]T, 0, len(items)), NextCursor: cursor}
	for _, item := range items {
		value, parseErr := parse(item)
		if parseErr != nil {
			return nil, parseErr
		}
		result.Items = append(result.Items, value)
	}
	return result, nil
}

func artifactPath(prefix, id string) (string, error) {
	if id == "search" {
		return "", badRequest("'search' cannot be looked up by name; use the resource ID")
	}
	segment, err := pathSegment(id)
	if err != nil {
		return "", err
	}
	return prefix + "/" + segment, nil
}
