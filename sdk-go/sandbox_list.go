// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"iter"
)

// ListSandboxesOptions selects a page. Limit is 1–200; zero uses the server default.
// Filters belong to SearchSandboxesOptions, not ListSandboxesOptions.
type ListSandboxesOptions struct {
	// Cursor is the opaque continuation cursor.
	Cursor string
	// Limit is the maximum page size; zero uses the server default.
	Limit int
}

// List returns exactly one page, preferring search and remembering deployments
// that do not serve it. Only a 404 or SEARCH_NOT_CONFIGURED 501 enables fallback.
func (c *Client) List(ctx context.Context, opts *ListSandboxesOptions) (*Page[*Sandbox], error) {
	if err := c.ensureOpen(); err != nil {
		return nil, err
	}
	options := ListSandboxesOptions{}
	if opts != nil {
		options = *opts
	}
	query, err := pageQuery(options.Cursor, options.Limit)
	if err != nil {
		return nil, err
	}
	var raw JSONValue
	read := func(path string) error { return c.api().do(ctx, apiRequest{Path: path, Query: query, Result: &raw}) }
	if c.searchFallback.Load() {
		err = read("/sandboxes")
		var detail *Error
		if errors.As(err, &detail) && detail.StatusCode == 410 && detail.Code == CodeGone {
			c.searchFallback.Store(false)
		} else {
			if err != nil {
				return nil, err
			}
			return c.listPage(ctx, raw)
		}
	}
	err = read("/sandboxes/search")
	if errorStatus(err) == 404 || (errorStatus(err) == 501 && errors.Is(err, ErrSearchNotConfigured)) {
		c.searchFallback.Store(true)
		err = read("/sandboxes")
	}
	if err != nil {
		return nil, err
	}
	return c.listPage(ctx, raw)
}

// ListAll traverses pages until exhaustion, a repeated cursor, an error, or
// caller cancellation. Breaking iteration makes no further requests.
func (c *Client) ListAll(ctx context.Context, opts *ListSandboxesOptions) iter.Seq2[*Sandbox, error] {
	options := ListSandboxesOptions{}
	if opts != nil {
		options = *opts
	}
	return paginate(ctx, options.Cursor, func(ctx context.Context, cursor string) (*Page[*Sandbox], error) {
		pageOptions := options
		pageOptions.Cursor = cursor
		return c.List(ctx, &pageOptions)
	})
}

func (c *Client) listPage(ctx context.Context, raw JSONValue) (*Page[*Sandbox], error) {
	page, err := parseSandboxPage(raw, false)
	if err != nil {
		return nil, err
	}
	items, err := c.pageHandles(ctx, page.observations)
	if err != nil {
		return nil, err
	}
	return &Page[*Sandbox]{Items: items, NextCursor: page.cursor}, nil
}
