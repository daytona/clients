// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/regions"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

type regionalListSource struct {
	home    *api.Client
	catalog *workload.CatalogCache
	region  string
	// raws keeps each item's API JSON for the output.
	raws *rawIndex
}

func newSandboxListCommand() *cobra.Command {
	var flags pageFlags
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List sandboxes",
		Long:  "List sandboxes across regions.\nExamples:\n  daytona sandbox list\n  daytona sandbox list --region region-a --limit 20",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			a := FromContext(cmd.Context())
			home, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			source := regionalListSource{home: home, catalog: workload.NewCatalogCache(), region: a.Globals.Region, raws: newRawIndex()}
			fetch := func(ctx context.Context, cursor string) (output.List[gen.SandboxListItem], error) {
				return source.fetchSandboxPage(ctx, listPageRequest{limit: flags.limit, cursor: cursor})
			}
			var list output.List[gen.SandboxListItem]
			if flags.all {
				list, err = collectPaged(cmd.Context(), flags.cursor, fetch)
			} else {
				list, err = fetch(cmd.Context(), flags.cursor)
			}
			if err != nil {
				return err
			}
			list.Items = regions.Unique(list.Items, func(item gen.SandboxListItem) string { return item.Id })
			inRegion, err := source.regionFilter(cmd.Context())
			if err != nil {
				return err
			}
			list.Items = keepItems(list.Items, func(item gen.SandboxListItem) bool { return inRegion(item.RegionId) })
			if err := listOutRaw(a.Printer(), rawList(source.raws, list), sandboxListTable); err != nil {
				return err
			}
			return regions.PartialError(list)
		},
	}
	addPageFlags(cmd, &flags)
	addRegionFlag(cmd, "list only the sandboxes in this region or a region inside it (default: every region); a page can then hold fewer than --limit items")
	return cmd
}

func newSandboxGetCommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "get <id>",
		Short: "Get one sandbox",
		Long:  "Get one sandbox by id.\nExample:\n  daytona sandbox get <id>",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return printSandboxDetail(cmd.Context(), args[0])
		},
	}
	return cmd
}

func newSandboxWaitCommand() *cobra.Command {
	status := "running"
	cmd := &cobra.Command{
		Use:   "wait <id>",
		Short: "Wait for a sandbox status",
		Long:  "Wait for a sandbox to reach a settled status: running, stopped, paused or deleted.\nThe SDK state names started (running) and destroyed (deleted) are accepted too.\nExample:\n  daytona sandbox wait <id> --for running",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			target, err := sandboxWaitTarget(status)
			if err != nil {
				return err
			}
			a := FromContext(cmd.Context())
			home, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			cache := workload.NewCatalogCache()
			resolved, err := workload.ResolveSandbox(cmd.Context(), home, cache, args[0])
			if err != nil {
				return err
			}
			timeout, err := workload.ParseTimeout(a.Globals.Timeout)
			if err != nil {
				return err
			}
			detail, err := workload.WaitForSandbox(cmd.Context(), resolved.Client, workload.SandboxWaitOptions{
				ID:                resolved.Detail.Id,
				Target:            target,
				Timeout:           timeout,
				PauseFailureFails: target == "paused" && resolved.Detail.Class == workload.ClassLinux,
			})
			if err != nil {
				return err
			}
			return a.Printer().Object(detail, sandboxDetailKV(detail.V))
		},
	}
	cmd.Flags().StringVar(&status, "for", status, "status to wait for: running, stopped, paused or deleted (or the SDK names started, destroyed)")
	flagPlaceholder(cmd.Flags(), "for", "STATUS")
	addTimeoutFlag(cmd, defaultWaitTimeout, "how long to wait (Go duration)")
	return cmd
}

// checkSandboxCursorRegions refuses a --cursor that names a region the
// catalog does not have: listing would silently start over at page one.
func checkSandboxCursorRegions(cat *regions.Catalog, cursor string) error {
	if cat == nil || len(cat.Regions) == 0 {
		return nil
	}
	for region := range parseSandboxCursor(cursor) {
		if region == "" {
			continue
		}
		if canonical := cat.Find(region); canonical == nil || canonical.Id != region {
			return cerr.Usage("invalid --cursor: it names region %q, which the region catalog does not have", region).
				WithHint("the cursor is stale or was not printed by `daytona sandbox list`; list again without --cursor")
		}
	}
	return nil
}

// sandboxWaitTargets maps each `sandbox wait --for` value to the API status it
// waits for: the settled statuses, and the SDKs' names for two of them.
// Transient statuses are not targets: a wait could miss them and run to its
// timeout.
var sandboxWaitTargets = map[string]string{
	"running": "running", "stopped": "stopped", "paused": "paused", "deleted": "deleted",
	"started": "running", "destroyed": "deleted",
}

func sandboxWaitTarget(value string) (string, error) {
	if target, ok := sandboxWaitTargets[value]; ok {
		return target, nil
	}
	return "", cerr.Usage("invalid --for %q: want running, stopped, paused or deleted (or started, destroyed)", value)
}

func (source regionalListSource) fetchSandboxPage(ctx context.Context, page listPageRequest) (output.List[gen.SandboxListItem], error) {
	cat, err := source.catalog.Fetch(ctx, source.home)
	if err != nil {
		return output.List[gen.SandboxListItem]{}, err
	}
	if err := checkSandboxCursorRegions(cat, page.cursor); err != nil {
		return output.List[gen.SandboxListItem]{}, err
	}
	cat = cat.ForListCursor(source.home.BaseURL, page.cursor)
	scope := newRegionalPageCursor(page.cursor, cat.ListRegionKeys(source.home.BaseURL, source.region))
	raw, err := regions.FanOut(ctx, source.home, cat, source.region,
		func(ctx context.Context, client *api.Client, currentRegion string) (regions.Page[gen.SandboxListItem], error) {
			state := requestedSandboxCursor(currentRegion, scope.requested)
			if state.done {
				return regions.Page[gen.SandboxListItem]{Items: []gen.SandboxListItem{}}, nil
			}
			query := listQuery(pageFlags{limit: page.limit}, state.cursor)
			resp, err := getRawList[gen.SandboxListItem](ctx, client, "/sandboxes", query, "sandboxes")
			if err != nil {
				return regions.Page[gen.SandboxListItem]{}, err
			}
			items := api.Values(resp.Items)
			for _, item := range resp.Items {
				addRaw(source.raws, item)
			}
			if scope.streams != nil {
				rows := make(map[string]string, len(items))
				for _, item := range items {
					rows[item.Id] = item.Cursor
				}
				scope.recordStream(currentRegion, rows, resp.NextCursor != "")
			}
			return regions.Page[gen.SandboxListItem]{Items: items, NextCursor: resp.NextCursor}, nil
		},
		func(a, b gen.SandboxListItem) bool {
			if a.CreatedAt == nil || b.CreatedAt == nil {
				return a.CreatedAt != nil
			}
			if a.CreatedAt.Equal(*b.CreatedAt) {
				return a.Id > b.Id
			}
			return a.CreatedAt.After(*b.CreatedAt)
		},
	)
	if err != nil {
		return output.List[gen.SandboxListItem]{}, err
	}
	raw.Items = regions.Unique(raw.Items, func(item gen.SandboxListItem) string { return item.Id })
	return trimSandboxPage(raw, scope, page.limit), nil
}

func printSandboxDetail(ctx context.Context, id string) error {
	a := FromContext(ctx)
	home, _, err := a.WorkloadClient(ctx, pickProject(a))
	if err != nil {
		return err
	}
	resolved, err := workload.ResolveSandbox(ctx, home, workload.NewCatalogCache(), id)
	if err != nil {
		return err
	}
	return a.Printer().Object(resolved.Raw, sandboxDetailKV(resolved.Detail))
}

// regionFilter reports whether a region id is the --region the list was
// narrowed to or a region inside it; every region matches without --region.
// The list API has no region filter: the CLI asks every API that serves the
// region or a region inside it, then drops the other regions' items (regions
// without their own API base URL share one), while the cursor still advances
// over each API's whole page.
func (source regionalListSource) regionFilter(ctx context.Context) (func(string) bool, error) {
	if source.region == "" {
		return func(string) bool { return true }, nil
	}
	cat, err := source.catalog.Fetch(ctx, source.home)
	if err != nil {
		return nil, err
	}
	selected := cat.Find(source.region)
	if selected == nil {
		return nil, cerr.Usage("unknown region %q", source.region).WithHint("`daytona region list` shows the regions")
	}
	return func(id string) bool { return cat.InScope(selected.Id, id) }, nil
}

// keepItems returns the items keep accepts, in order.
func keepItems[T any](items []T, keep func(T) bool) []T {
	out := items[:0:0]
	for _, item := range items {
		if keep(item) {
			out = append(out, item)
		}
	}
	if out == nil {
		out = []T{}
	}
	return out
}
