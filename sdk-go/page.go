// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"iter"
	"net/url"
	"strconv"
	"unicode/utf8"
)

// Page is one server page. An empty NextCursor ends pagination. Items belong to
// the caller; mutating the slice does not change any handle's stored metadata.
type Page[T any] struct {
	// Items contains this page's results.
	Items []T
	// NextCursor is empty at the end.
	NextCursor string
}

// SearchPage is a page with a server-reported hit count and its precision.
type SearchPage[T any] struct {
	// Page contains the results and continuation cursor.
	Page[T]
	// Total is the server-reported hit count.
	Total int64
	// TotalRelation describes count precision.
	TotalRelation TotalRelation
	// RawTotalRelation preserves the server's relation verbatim.
	RawTotalRelation string
}

// TotalRelation describes whether a search count is exact or a lower bound.
type TotalRelation string

const (
	// TotalRelationEqual means Total is exact.
	TotalRelationEqual TotalRelation = "eq"
	// TotalRelationGreaterOrEqual means Total is a lower bound.
	TotalRelationGreaterOrEqual TotalRelation = "gte"
	// TotalRelationUnknown preserves a future relation in RawTotalRelation.
	TotalRelationUnknown TotalRelation = "unknown"
)

func paginate[T any](ctx context.Context, cursor string,
	fetch func(context.Context, string) (*Page[T], error),
) iter.Seq2[T, error] {
	return func(yield func(T, error) bool) {
		next := cursor
		seen := make(map[string]bool)
		if next != "" {
			seen[next] = true
		}
		var zero T
		for {
			if err := ctx.Err(); err != nil {
				yield(zero, transportError(ctx, err))
				return
			}
			page, err := fetch(ctx, next)
			if err != nil {
				yield(zero, err)
				return
			}
			for _, item := range page.Items {
				if contextErr := ctx.Err(); contextErr != nil {
					yield(zero, transportError(ctx, contextErr))
					return
				}
				if !yield(item, nil) {
					return
				}
			}
			next = page.NextCursor
			if next == "" || seen[next] {
				return
			}
			seen[next] = true
		}
	}
}

func pageQuery(cursor string, limit int) (url.Values, error) {
	if !utf8.ValidString(cursor) {
		return nil, badRequest("cursor must be valid UTF-8")
	}
	if limit < 0 || limit > 200 {
		return nil, badRequest("Limit must be an integer between 1 and 200")
	}
	params := make(url.Values)
	if cursor != "" {
		params.Set("cursor", cursor)
	}
	if limit != 0 {
		params.Set("limit", strconv.Itoa(limit))
	}
	return params, nil
}
