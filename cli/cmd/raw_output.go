// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
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

// rawIndex remembers the API's JSON of list items for lists whose merging
// and paging work on the decoded values (the region fan-out). An item is
// keyed by a digest of its whole decoded value, so the JSON paired with a row is a copy
// of that row (the same id fetched from two regions can differ in
// `region_id` or `cursor`); among copies that decode alike the smallest JSON
// is kept, so the choice does not depend on which region answered first.
type rawIndex struct {
	mu    sync.Mutex
	byKey map[string]json.RawMessage
}

func newRawIndex() *rawIndex { return &rawIndex{byKey: map[string]json.RawMessage{}} }

// rawKey is a fixed-size digest of v's encoding, so the index does not hold
// a second copy of every row.
func rawKey(v any) string {
	encoded, err := json.Marshal(v)
	if err != nil {
		return ""
	}
	sum := sha256.Sum256(encoded)
	return string(sum[:])
}

// addRaw remembers item's JSON.
func addRaw[T any](x *rawIndex, item api.Raw[T]) {
	if x == nil {
		return
	}
	key := rawKey(item.V)
	if key == "" {
		return
	}
	x.mu.Lock()
	defer x.mu.Unlock()
	if kept, ok := x.byKey[key]; !ok || bytes.Compare(item.JSON, kept) < 0 {
		x.byKey[key] = item.JSON
	}
}

// rawList pairs each decoded item with the JSON it came from.
func rawList[T any](x *rawIndex, l output.List[T]) output.List[api.Raw[T]] {
	out := output.List[api.Raw[T]]{Items: make([]api.Raw[T], 0, len(l.Items)), NextCursor: l.NextCursor, Incomplete: l.Incomplete, Errors: l.Errors}
	for _, item := range l.Items {
		r := api.Raw[T]{V: item}
		if x != nil {
			x.mu.Lock()
			r.JSON = x.byKey[rawKey(item)]
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
	if object == nil { // a JSON null
		object = map[string]json.RawMessage{}
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	object[key] = encoded
	return json.Marshal(object)
}

// writeOnlyFields are the request fields that carry a credential: the CLI
// never prints them, whatever a response holds (the one exception to
// printing the API's JSON as it came). Matched case-insensitively.
var writeOnlyFields = struct{ secret, registry []string }{
	secret:   []string{"value"},
	registry: []string{"password"},
}

// withoutWriteOnly returns r with the fields named in keys (any case) removed
// from its top-level JSON object; r is unchanged when none is present.
func withoutWriteOnly[T any](r api.Raw[T], keys []string) api.Raw[T] {
	var object map[string]json.RawMessage
	if json.Unmarshal(r.JSON, &object) != nil || object == nil {
		return r
	}
	dropped := false
	for k := range object {
		for _, key := range keys {
			if strings.EqualFold(k, key) {
				delete(object, k)
				dropped = true
			}
		}
	}
	if !dropped {
		return r
	}
	clean, err := json.Marshal(object)
	if err != nil {
		return api.Raw[T]{V: r.V}
	}
	return api.Raw[T]{V: r.V, JSON: clean}
}

// listWithoutWriteOnly applies withoutWriteOnly to every item.
func listWithoutWriteOnly[T any](l output.List[api.Raw[T]], keys []string) output.List[api.Raw[T]] {
	for i := range l.Items {
		l.Items[i] = withoutWriteOnly(l.Items[i], keys)
	}
	return l
}
