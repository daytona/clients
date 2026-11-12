// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"errors"
	"net/http"
	"net/url"

	"github.com/daytona/clients/cli/internal/api"
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
	if probe != nil && probe.notServed {
		return output.List[api.Raw[T]]{}, false, nil
	}
	list, err = getRawList[T](ctx, c, searchPath, query, field)
	if err != nil && searchNotServed(err) {
		if probe != nil {
			probe.notServed = true
		}
		return output.List[api.Raw[T]]{}, false, nil
	}
	return list, true, err
}
