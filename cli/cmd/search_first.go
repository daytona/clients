// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
)

// searchNotServed reports whether err, from a search route, means the
// deployment does not serve search: 501 SEARCH_NOT_CONFIGURED, or 404 from an
// API that predates the route. A 503 SEARCH_UNAVAILABLE (search configured but
// down) is not: it stays an error.
func searchNotServed(err error) bool {
	var apiErr *api.APIError
	if !errors.As(err, &apiErr) {
		return false
	}
	if apiErr.Status == http.StatusNotFound {
		return true
	}
	return apiErr.Status == http.StatusNotImplemented && apiErr.Code == "SEARCH_NOT_CONFIGURED"
}

// searchProbe remembers, for one command, that the deployment does not serve
// search, so the pages after the first go straight to the list route.
type searchProbe struct{ notServed bool }

// getRawSearchPage reads one page of searchPath, the project-wide search
// route that serves a list on a deployment with search (its list route
// answers 410 GONE there). served is false when the deployment does not serve
// search (searchNotServed, remembered in probe when it is not nil): the caller
// then reads its list route. Search results are eventually consistent.
func getRawSearchPage[T any](ctx context.Context, probe *searchProbe, c *api.Client, searchPath string, query url.Values, field string) (list output.List[api.Raw[T]], served bool, err error) {
	return searchPage[T](ctx, probe, c, searchPath, query, field, false)
}

// searchPage is getRawSearchPage. With itemFallback, a 200 that is not a page
// also means search is not served: an API that predates the secret and volume
// search routes answers GET /secrets/search and GET /volumes/search with the
// resource named "search". Other kinds keep reporting such a body.
func searchPage[T any](ctx context.Context, probe *searchProbe, c *api.Client, searchPath string, query url.Values, field string, itemFallback bool) (list output.List[api.Raw[T]], served bool, err error) {
	if probe != nil && probe.notServed {
		return output.List[api.Raw[T]]{}, false, nil
	}
	var body json.RawMessage
	err = c.Do(ctx, http.MethodGet, searchPath, query, nil, &body)
	if err != nil && searchNotServed(err) {
		if probe != nil {
			probe.notServed = true
		}
		return output.List[api.Raw[T]]{}, false, nil
	}
	if err != nil {
		return output.List[api.Raw[T]]{}, true, err
	}
	var envelope map[string]json.RawMessage
	if err := json.Unmarshal(body, &envelope); err != nil {
		return output.List[api.Raw[T]]{}, true, fmt.Errorf("decode GET %s response: %w", searchPath, err)
	}
	if items, ok := envelope[field]; itemFallback && (!ok || !isJSONArray(items)) {
		if probe != nil {
			probe.notServed = true
		}
		return output.List[api.Raw[T]]{}, false, nil
	}
	page, err := api.DecodePage[T](body, field)
	if err != nil {
		return output.List[api.Raw[T]]{}, true, fmt.Errorf("decode GET %s response: %w", searchPath, err)
	}
	return output.List[api.Raw[T]]{Items: page.Items, NextCursor: page.NextCursor}, true, nil
}

func getRawSearchOrListPage[T any](ctx context.Context, probe *searchProbe, c *api.Client, searchPath, listPath string, query url.Values, field string) (output.List[api.Raw[T]], error) {
	remembered := probe.notServed
	if page, served, err := searchPage[T](ctx, probe, c, searchPath, query, field, true); served {
		return page, err
	}
	page, listErr := getRawList[T](ctx, c, listPath, query, field)
	if !remembered || !listRetired(listErr) {
		return page, listErr
	}
	probe.notServed = false
	page, served, err := searchPage[T](ctx, probe, c, searchPath, query, field, true)
	if !served {
		// Neither route serves the list: report the list route's 410, not an empty page.
		return page, listErr
	}
	return page, err
}

func isJSONArray(raw json.RawMessage) bool {
	var items []json.RawMessage
	return json.Unmarshal(raw, &items) == nil && items != nil
}

func listRetired(err error) bool {
	var apiErr *api.APIError
	return errors.As(err, &apiErr) && apiErr.Status == http.StatusGone && apiErr.Code == "GONE"
}

// refuseSearchName refuses the reference "search" for an image or snapshot
// get: GET /images/search and GET /snapshots/search are the search routes, so
// a get of that name would reach the search route, not the item. The item's
// id still reads it.
func refuseSearchName(kind, ref string) error {
	if ref != "search" {
		return nil
	}
	return cerr.Usage("'search' cannot be looked up by name; it is reserved for the search route — use the %s ID", kind)
}

func refuseResourceSearchName(kind, ref string) error {
	if ref != "search" {
		return nil
	}
	return cerr.Usage("'search' cannot be used as a %s name; it is reserved for the search route", kind)
}
