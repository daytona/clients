// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package regions

import (
	"context"
	"sort"
	"sync"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
)

// FanOut queries each distinct normalized API base URL once, except when
// ForListCursor preserves independent alias streams. The first region
// for a URL names that endpoint's cursor and errors. The `only` argument
// restricts the selection to one region.
// Failed API endpoints produce incomplete results, which PartialError maps to
// exit 7. A cancelled or expired ctx is the caller's, not an endpoint's: it is
// returned as the error (exit 130 or 8) instead of a partial list.
func FanOut[T any](ctx context.Context, home *api.Client, cat *Catalog, only string,
	fetch func(ctx context.Context, c *api.Client, region string) (Page[T], error),
	less func(a, b T) bool) (output.List[T], error) {
	if callerErr := cerr.FromContext(ctx); callerErr != nil {
		return output.List[T]{}, callerErr
	}
	targets := []gen.Region{}
	if cat != nil {
		if only != "" {
			if r := cat.Find(only); r != nil {
				if cat.HasListAliases() {
					for _, alias := range cat.OrderedRegions() {
						if cat.listAliases[alias.Id] && BaseFor(alias, home.BaseURL) == BaseFor(*r, home.BaseURL) {
							targets = append(targets, alias)
						}
					}
				}
				if len(targets) == 0 {
					targets = append(targets, *r)
				}
			} else {
				return output.List[T]{Incomplete: true, Errors: []output.RegionError{{Region: only, Message: "unknown region (see `daytona region list`)"}}}, nil
			}
		} else {
			seen := map[string]bool{}
			for _, r := range cat.OrderedRegions() {
				if cat.HasListAliases() && !cat.listAliases[r.Id] {
					continue
				}
				base := BaseFor(r, home.BaseURL)
				if cat.HasListAliases() || !seen[base] {
					seen[base] = true
					targets = append(targets, r)
				}
			}
		}
	}
	if len(targets) == 0 {
		p, err := fetch(ctx, home, only)
		if callerErr := cerr.FromContext(ctx); callerErr != nil {
			return output.List[T]{}, callerErr
		}
		l := output.List[T]{Items: p.Items, NextCursor: p.NextCursor}
		if err != nil {
			l.Incomplete = true
			l.Errors = append(l.Errors, toRegionError(only, err))
		}
		if l.Items == nil {
			l.Items = []T{}
		}
		return l, nil
	}

	type res struct {
		region string
		page   Page[T]
		err    error
	}
	results := make([]res, len(targets))
	var wg sync.WaitGroup
	for i, r := range targets {
		wg.Add(1)
		go func(i int, r gen.Region) {
			defer wg.Done()
			c := home.WithBaseURL(BaseFor(r, home.BaseURL))
			p, err := fetch(ctx, c, r.Id)
			results[i] = res{r.Id, p, err}
		}(i, r)
	}
	wg.Wait()
	if callerErr := cerr.FromContext(ctx); callerErr != nil {
		return output.List[T]{}, callerErr
	}

	l := output.List[T]{Items: []T{}}
	for _, r := range results {
		if r.err != nil {
			l.Incomplete = true
			l.Errors = append(l.Errors, toRegionError(r.region, r.err))
			continue
		}
		l.Items = append(l.Items, r.page.Items...)
		if r.page.NextCursor != "" {
			if l.NextCursor != "" {
				l.NextCursor += ","
			}
			l.NextCursor += r.region + ":" + r.page.NextCursor
		}
	}
	if less != nil {
		sort.SliceStable(l.Items, func(i, j int) bool { return less(l.Items[i], l.Items[j]) })
	}
	return l, nil
}

// Unique keeps the first item for each ID, preserving merged sort order.
func Unique[T any](items []T, id func(T) string) []T {
	seen := make(map[string]bool, len(items))
	out := make([]T, 0, len(items))
	for _, item := range items {
		key := id(item)
		if !seen[key] {
			seen[key] = true
			out = append(out, item)
		}
	}
	return out
}
