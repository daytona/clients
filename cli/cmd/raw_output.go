// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"sync"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/output"
)

// JSON and YAML output print what the API sent: object commands print the
// response body, lists print each item as the API sent it inside the CLI's
// envelope. Tables use the decoded values. See api.Raw.

// listOutRaw prints a list of API items: their JSON in JSON/YAML, tbl's table
// of the decoded values for humans.
func listOutRaw[T any](p output.Printer, l output.List[api.Raw[T]], tbl func([]T) output.Table) error {
	return output.ListOut(p, l, func(items []api.Raw[T]) output.Table { return tbl(api.Values(items)) })
}

// rawIndex remembers the API's JSON of list items by id, for lists whose
// merging and paging work on the decoded values (the region fan-out).
type rawIndex struct {
	mu   sync.Mutex
	byID map[string]json.RawMessage
}

func newRawIndex() *rawIndex { return &rawIndex{byID: map[string]json.RawMessage{}} }

func (x *rawIndex) add(id string, raw json.RawMessage) {
	if x == nil {
		return
	}
	x.mu.Lock()
	defer x.mu.Unlock()
	if _, ok := x.byID[id]; !ok {
		x.byID[id] = raw
	}
}

// rawList pairs each decoded item with the JSON it came from.
func rawList[T any](x *rawIndex, l output.List[T], id func(T) string) output.List[api.Raw[T]] {
	out := output.List[api.Raw[T]]{Items: make([]api.Raw[T], 0, len(l.Items)), NextCursor: l.NextCursor, Incomplete: l.Incomplete, Errors: l.Errors}
	for _, item := range l.Items {
		r := api.Raw[T]{V: item}
		if x != nil {
			x.mu.Lock()
			r.JSON = x.byID[id(item)]
			x.mu.Unlock()
		}
		out.Items = append(out.Items, r)
	}
	return out
}

// getRawList reads one page of a list endpoint whose items sit in field.
func getRawList[T any](ctx context.Context, c *api.Client, path string, query url.Values, field string) (output.List[api.Raw[T]], error) {
	var body json.RawMessage
	if err := c.Do(ctx, http.MethodGet, path, query, nil, &body); err != nil {
		return output.List[api.Raw[T]]{}, err
	}
	page, err := api.DecodePage[T](body, field)
	if err != nil {
		return output.List[api.Raw[T]]{}, fmt.Errorf("decode GET %s response: %w", path, err)
	}
	return output.List[api.Raw[T]]{Items: page.Items, NextCursor: page.NextCursor}, nil
}

// getRawWhole reads a list endpoint that returns every item at once.
func getRawWhole[T any](ctx context.Context, c *api.Client, path string, query url.Values, field string) (output.List[api.Raw[T]], error) {
	l, err := getRawList[T](ctx, c, path, query, field)
	l.NextCursor = ""
	return l, err
}

// withField returns the JSON object raw with key set to value: a CLI-added
// field next to the API's own.
func withField(raw json.RawMessage, key string, value any) (json.RawMessage, error) {
	var object map[string]json.RawMessage
	if err := json.Unmarshal(raw, &object); err != nil {
		return nil, err
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	object[key] = encoded
	return json.Marshal(object)
}
