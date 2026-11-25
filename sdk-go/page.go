// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"iter"
	"math/rand/v2"
	"net/http"
	"net/url"
	"strconv"
	"time"
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

// Every read of one page, the first included; the pause before the second
// and the third read (each 80-100 % of it); and the longest server retry hint
// a page read waits for (a longer one surfaces the error). Python and
// TypeScript use the same policy.
const pageReadAttempts = 3

var pageReadDelays = [...]time.Duration{500 * time.Millisecond, time.Second}

const pageReadMaxDelay = 10 * time.Second

// pageRetryPause and pageRetryJitter are seams for tests.
var (
	pageRetryPause  = contextUploadPause
	pageRetryJitter = rand.Float64
)

// pageRetryDelay is the pause before read retry+2 of a page after err, or
// false when err is not retried: only a dropped connection and HTTP 429, 502,
// 503 and 504 are (not an organization limit, and not the client's own
// request timeout, which already bounds each read).
func pageRetryDelay(err error, retry int) (time.Duration, bool) {
	if retry+1 >= pageReadAttempts || errors.Is(err, ErrLimitExceeded) || errors.Is(err, ErrClientTimeout) {
		return 0, false
	}
	var detail *Error
	if !errors.As(err, &detail) {
		return 0, false
	}
	switch {
	case errors.Is(err, ErrConnection):
	case detail.StatusCode == http.StatusTooManyRequests, detail.StatusCode == http.StatusBadGateway,
		detail.StatusCode == http.StatusServiceUnavailable, detail.StatusCode == http.StatusGatewayTimeout:
	default:
		return 0, false
	}
	if detail.RetryAfter > pageReadMaxDelay {
		return 0, false
	}
	delay := time.Duration(float64(pageReadDelays[retry]) * (0.8 + 0.2*pageRetryJitter()))
	return max(delay, detail.RetryAfter), true
}

// readPage reads one page, again after a transient failure (see
// pageRetryDelay); the *Error that ends it carries PageCursor: cursor, the
// cursor to resume the walk with.
func readPage[T any](ctx context.Context, cursor string,
	fetch func(context.Context, string) (*Page[T], error),
) (*Page[T], error) {
	for retry := 0; ; retry++ {
		page, err := fetch(ctx, cursor)
		if err == nil {
			return page, nil
		}
		if delay, retryable := pageRetryDelay(err, retry); retryable {
			// A pause cut short by the caller's context ends the walk with
			// that cancellation instead.
			if err = pageRetryPause(ctx, delay); err == nil {
				continue
			}
		}
		var detail *Error
		if errors.As(err, &detail) {
			detail.PageCursor = cursor
		}
		return nil, err
	}
}

// paginate walks every page from cursor on, reading each with readPage.
func paginate[T any](ctx context.Context, cursor string,
	fetch func(context.Context, string) (*Page[T], error),
) iter.Seq2[T, error] {
	return walkPages(ctx, cursor, func(ctx context.Context, cursor string) (*Page[T], error) {
		return readPage(ctx, cursor, fetch)
	})
}

// walkPages yields every item of every page from cursor on, guarding against a
// cursor cycle; an empty or repeated next cursor ends the walk.
func walkPages[T any](ctx context.Context, cursor string,
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
		return nil, badRequest("Cursor must be valid UTF-8")
	}
	if limit < 0 || limit > 200 {
		return nil, badRequest("Limit must be 0 (server default) or 1 to 200")
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
