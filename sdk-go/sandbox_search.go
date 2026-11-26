// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"iter"
	"maps"
	"net/http"
	"net/url"
	"slices"
	"strings"
)

// Search returns one page and its hit count. Unlike List it never falls back
// when search is unavailable. ErrSearchNotConfigured and ErrSearchUnavailable
// distinguish those deployment conditions.
func (c *Client) Search(ctx context.Context, opts *SearchSandboxesOptions) (*SearchPage[*Sandbox], error) {
	if err := c.ensureOpen(); err != nil {
		return nil, err
	}
	options := SearchSandboxesOptions{}
	if opts != nil {
		options = *opts
	}
	query, err := searchQuery(options)
	if err != nil {
		return nil, err
	}
	return c.searchPage(ctx, apiRequest{Path: "/sandboxes/search", Query: query})
}

func (c *Client) searchPage(ctx context.Context, request apiRequest) (*SearchPage[*Sandbox], error) {
	page, err := c.searchObservations(ctx, request)
	if err != nil {
		return nil, err
	}
	items, err := c.pageHandles(ctx, page.observations)
	if err != nil {
		return nil, err
	}
	relation := TotalRelationUnknown
	switch page.relation {
	case "eq":
		relation = TotalRelationEqual
	case "gte":
		relation = TotalRelationGreaterOrEqual
	}
	return &SearchPage[*Sandbox]{
		Page: Page[*Sandbox]{Items: items, NextCursor: page.cursor}, Total: page.total,
		TotalRelation: relation, RawTotalRelation: page.relation,
	}, nil
}

func (c *Client) searchObservations(ctx context.Context, request apiRequest) (sandboxPage, error) {
	var raw JSONValue
	spec := request
	spec.Result = &raw
	if err := c.api().do(ctx, spec); err != nil {
		return sandboxPage{}, err
	}
	return parseSandboxPage(raw, true)
}

// SearchAll traverses Search results lazily. The options are snapshotted at the
// call; an error is yielded once and terminates iteration.
func (c *Client) SearchAll(ctx context.Context, opts *SearchSandboxesOptions) iter.Seq2[*Sandbox, error] {
	options := snapshotSearchOptions(opts)
	return paginate(ctx, options.Cursor, func(ctx context.Context, cursor string) (*Page[*Sandbox], error) {
		pageOptions := options
		pageOptions.Cursor = cursor
		page, err := c.Search(ctx, &pageOptions)
		if err != nil {
			return nil, err
		}
		return &page.Page, nil
	})
}

func snapshotSearchOptions(opts *SearchSandboxesOptions) SearchSandboxesOptions {
	if opts == nil {
		return SearchSandboxesOptions{}
	}
	result := *opts
	result.Labels = maps.Clone(opts.Labels)
	result.States = slices.Clone(opts.States)
	result.Classes = slices.Clone(opts.Classes)
	result.Regions = slices.Clone(opts.Regions)
	result.Spot = clonePointer(opts.Spot)
	result.Persistent = clonePointer(opts.Persistent)
	result.MinCores, result.MaxCores = clonePointer(opts.MinCores), clonePointer(opts.MaxCores)
	result.MinMemoryMiB, result.MaxMemoryMiB = clonePointer(opts.MinMemoryMiB), clonePointer(opts.MaxMemoryMiB)
	result.MinDiskMiB, result.MaxDiskMiB = clonePointer(opts.MinDiskMiB), clonePointer(opts.MaxDiskMiB)
	return result
}

// FindByNameOptions selects a region. Empty, whitespace or "local" (ignoring
// case) uses the default region; other values are sent unchanged.
type FindByNameOptions struct {
	// Region selects a region; empty or "local" uses the default.
	Region string
}

// FindByName returns every name-label match across all pages, excluding deleted
// and deleting sandboxes. Names are not unique. This is the intentional
// all-pages exception to the SDK's one-page list methods.
func (c *Client) FindByName(ctx context.Context, name string, opts *FindByNameOptions) ([]*Sandbox, error) {
	if err := c.ensureOpen(); err != nil {
		return nil, err
	}
	if strings.TrimSpace(name) == "" {
		return nil, badRequest("name must be a non-blank string")
	}
	headers := make(http.Header)
	region := ""
	if opts != nil {
		region = opts.Region
	}
	switch strings.ToLower(strings.TrimSpace(region)) {
	case "", "local":
	default:
		if err := headerText(region, "region"); err != nil {
			return nil, err
		}
		headers.Set("X-Daytona-Region", region)
	}
	query, err := searchQuery(SearchSandboxesOptions{Labels: map[string]string{"name": name}, Limit: 100})
	if err != nil {
		return nil, err
	}
	items := make([]*Sandbox, 0)
	fetch := func(ctx context.Context, cursor string) (*Page[*Sandbox], error) {
		params := make(url.Values, len(query)+1)
		for key, values := range query {
			params[key] = values
		}
		if cursor != "" {
			params.Set("cursor", cursor)
		}
		request := apiRequest{Path: "/sandboxes/search", Query: params, Headers: headers}
		page, fetchErr := c.searchObservations(ctx, request)
		if fetchErr != nil {
			return nil, fetchErr
		}
		live := make([]sandboxObservation, 0, len(page.observations))
		for _, observation := range page.observations {
			switch observation.info.RawState {
			case "deleted", "deleting":
			default:
				live = append(live, observation)
			}
		}
		handles, handleErr := c.pageHandles(ctx, live)
		if handleErr != nil {
			return nil, handleErr
		}
		return &Page[*Sandbox]{Items: handles, NextCursor: page.cursor}, nil
	}
	for sandbox, iterErr := range paginate(ctx, "", fetch) {
		if iterErr != nil {
			return nil, iterErr
		}
		items = append(items, sandbox)
	}
	return items, nil
}
