// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/mgmt"
	"github.com/daytona/clients/cli/internal/output"
)

// collectPaged follows every page from `start` (the --cursor the caller was
// given, "" for the first page) until the server returns no continuation.
func collectPaged[T any](ctx context.Context, start string, fetch func(context.Context, string) (output.List[T], error)) (output.List[T], error) {
	all := output.List[T]{Items: []T{}}
	cursor := start
	seen := map[string]struct{}{}
	failed := map[string]struct{}{}
	if start != "" {
		seen[start] = struct{}{}
	}
	for {
		page, err := fetch(ctx, cursor)
		if err != nil {
			return output.List[T]{}, err
		}
		all.Items = append(all.Items, page.Items...)
		all.Incomplete = all.Incomplete || page.Incomplete
		for _, e := range page.Errors {
			// A failed region is retried on every page while its siblings advance.
			if _, dup := failed[e.Region]; !dup {
				failed[e.Region] = struct{}{}
				all.Errors = append(all.Errors, e)
			}
		}
		if page.NextCursor == "" {
			return all, nil
		}
		if _, ok := seen[page.NextCursor]; ok {
			// Failed streams keep their input cursor. Once the working streams
			// stop advancing, return their items plus the failed continuation,
			// preserving partial-result exit 7 instead of a pagination failure.
			if page.Incomplete {
				all.NextCursor = page.NextCursor
				return all, nil
			}
			return output.List[T]{}, cerr.New(cerr.ExitFailure, "pagination cursor repeated: %s", page.NextCursor)
		}
		seen[page.NextCursor] = struct{}{}
		cursor = page.NextCursor
	}
}

// mgmtListPage reads what the page flags ask for: one page (--limit,
// --cursor), or every page from --cursor on with --all.
func mgmtListPage[T any](ctx context.Context, flags pageFlags, fetch func(ctx context.Context, cursor string, limit int64) (mgmt.Page[T], error)) (output.List[api.Raw[T]], error) {
	one := func(ctx context.Context, cursor string) (output.List[api.Raw[T]], error) {
		page, err := fetch(ctx, cursor, flags.limit)
		if err != nil {
			return output.List[api.Raw[T]]{}, err
		}
		return output.List[api.Raw[T]]{Items: page.Items, NextCursor: page.NextCursor}, nil
	}
	if flags.all {
		return collectPaged(ctx, flags.cursor, one)
	}
	return one(ctx, flags.cursor)
}
