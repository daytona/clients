// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package regions

import (
	"context"
	"sort"
	"strings"
	"sync"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
)

// FanOut queries each distinct normalized API base URL once, except when
// ForListCursor preserves independent alias streams. The first region
// for a URL names that endpoint's cursor and errors. The `only` argument
// restricts the selection to the endpoints that serve that region or a
// region inside it (the caller filters their items by region).
// An unknown `only` region is a usage error before any list request. When
// some endpoints fail, the result is incomplete (PartialError maps it to exit
// 7). When every endpoint fails, the command fails: with the endpoint's own
// error when there was one endpoint, else with an error that keeps the first
// endpoint's exit code and server metadata and whose message lists every
// endpoint's failure. A cancelled or expired ctx is the caller's, not an
// endpoint's: it is returned as the error (exit 130 or 8) instead of a
// partial list.
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
				scope := cat.scopeBases(home.BaseURL, only)
				if cat.HasListAliases() {
					for _, alias := range cat.OrderedRegions() {
						if _, ok := scope[BaseFor(alias, home.BaseURL)]; ok && cat.listAliases[alias.Id] {
							targets = append(targets, alias)
						}
					}
				}
				if len(targets) == 0 {
					// R's own endpoint first, then the other endpoints that
					// serve a region inside R, each named by scopeBases.
					targets = append(targets, *r)
					ownBase := BaseFor(*r, home.BaseURL)
					for _, region := range cat.OrderedRegions() {
						base := BaseFor(region, home.BaseURL)
						if base != ownBase && scope[base] == region.Id {
							targets = append(targets, region)
						}
					}
				}
			} else {
				return output.List[T]{}, cerr.Usage("unknown region %q", only).WithHint("`daytona region list` shows the regions")
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
		if err != nil {
			return output.List[T]{}, err
		}
		l := output.List[T]{Items: p.Items, NextCursor: p.NextCursor}
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

	var firstErr error
	failed := 0
	for _, r := range results {
		if r.err != nil {
			failed++
			if firstErr == nil {
				firstErr = r.err
			}
		}
	}
	if failed == len(results) {
		// Nothing answered: the failure is the command's, not a partial list.
		// Its exit code, code, source and trace id are the first region's;
		// the message names every region's failure.
		if len(results) == 1 {
			return output.List[T]{}, firstErr
		}
		parts := make([]string, 0, len(results))
		for _, r := range results {
			parts = append(parts, regionFailureText(toRegionError(r.region, r.err)))
		}
		first := cerr.DetailsOf(firstErr)
		e := cerr.Wrap(cerr.ExitCode(firstErr), firstErr, "every region failed — %s", strings.Join(parts, "; "))
		e.Hint = first.Hint
		return output.List[T]{}, e
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
