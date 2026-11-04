// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"net/http"
	"strconv"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/regions"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

type regionalListSource struct {
	home    *api.Client
	catalog *workload.CatalogCache
	region  string
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
			source := regionalListSource{home: home, catalog: workload.NewCatalogCache(), region: a.Globals.Region}
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
			if err := output.ListOut(a.Printer(), list, sandboxListTable); err != nil {
				return err
			}
			return regions.PartialError(list)
		},
	}
	addPageFlags(cmd, &flags)
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
		Long:  "Wait for a sandbox status.\nExample:\n  daytona sandbox wait <id> --for running",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
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
				ID:      resolved.Detail.Id,
				Target:  status,
				Timeout: timeout,
			})
			if err != nil {
				return err
			}
			return a.Printer().Object(detail, sandboxDetailKV(detail))
		},
	}
	cmd.Flags().StringVar(&status, "for", status, "status to wait for")
	return cmd
}

func (source regionalListSource) fetchSandboxPage(ctx context.Context, page listPageRequest) (output.List[gen.SandboxListItem], error) {
	cat, err := source.catalog.Fetch(ctx, source.home)
	if err != nil {
		return output.List[gen.SandboxListItem]{}, err
	}
	cat = cat.ForListCursor(source.home.BaseURL, page.cursor)
	scope := newRegionalPageCursor(page.cursor, cat.ListRegionKeys(source.home.BaseURL, source.region))
	raw := regions.FanOut(ctx, source.home, cat, source.region,
		func(ctx context.Context, client *api.Client, currentRegion string) (regions.Page[gen.SandboxListItem], error) {
			state := requestedSandboxCursor(currentRegion, scope.requested)
			if state.done {
				return regions.Page[gen.SandboxListItem]{Items: []gen.SandboxListItem{}}, nil
			}
			query := listQuery(pageFlags{limit: page.limit}, state.cursor)
			var resp gen.SandboxPage
			err := client.Do(ctx, http.MethodGet, "/sandboxes", query, nil, &resp)
			next := ""
			if resp.NextCursor != nil {
				next = *resp.NextCursor
			}
			if err == nil && scope.streams != nil {
				rows := make(map[string]string, len(resp.Sandboxes))
				for _, item := range resp.Sandboxes {
					rows[item.Id] = item.Cursor
				}
				scope.recordStream(currentRegion, rows, next != "")
			}
			return regions.Page[gen.SandboxListItem]{Items: resp.Sandboxes, NextCursor: next}, err
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
	raw.Items = regions.Unique(raw.Items, func(item gen.SandboxListItem) string { return item.Id })
	return trimSandboxPage(raw, scope, page.limit), nil
}

func sandboxListTable(items []gen.SandboxListItem) output.Table {
	rows := make([][]string, 0, len(items))
	for _, item := range items {
		rows = append(rows, []string{item.Id, item.Name, string(item.Class), item.Status, item.RegionId, sandboxArtifactSummary(item.Image, item.Snapshot), formatTimePtr(item.CreatedAt)})
	}
	return output.Table{Headers: []string{"ID", "NAME", "CLASS", "STATUS", "REGION", "ARTIFACT", "CREATED"}, Rows: rows}
}

// sandboxUnsized labels macOS resource sizes that are not available before first boot.
const sandboxUnsized = "sizing on first boot"

// formatCores prints a CPU amount in cores (1 core = 2 vCPUs) in its shortest
// exact form: 2, 0.5, 1.5.
func formatCores(cores float64) string {
	return strconv.FormatFloat(cores, 'f', -1, 64)
}

func sandboxDetailKV(detail gen.Sandbox) [][2]string {
	cpu := strings.TrimRight(strings.TrimRight(strconv.FormatFloat(detail.Cpu, 'f', 3, 64), "0"), ".")
	coresMax := formatCores(detail.CoresMax)
	memory := strconv.Itoa(int(detail.MemoryMib))
	if detail.Class == workload.ClassMacOS && detail.CoresMax == 0 {
		cpu, coresMax, memory = sandboxUnsized, sandboxUnsized, sandboxUnsized
	}
	kv := [][2]string{
		{"id", detail.Id},
		{"name", detail.Name},
		{"class", string(detail.Class)},
		{"status", detail.Status},
		{"region", detail.RegionId},
		{sandboxArtifactLabel(detail.Image, detail.Snapshot), sandboxArtifactSummary(detail.Image, detail.Snapshot)},
		{"cpu", cpu},
		{"cores_max", coresMax},
		{"memory_mib", memory},
		{"disk_mib", strconv.Itoa(int(detail.DiskMib))},
	}
	if detail.Class == workload.ClassMacOS {
		persistent := ""
		if detail.Persistent != nil {
			persistent = strconv.FormatBool(*detail.Persistent)
		}
		kv = append(kv, [2]string{"persistent", persistent})
		reservation := "-"
		if detail.ReservationId != nil && *detail.ReservationId != "" {
			reservation = *detail.ReservationId
		}
		kv = append(kv, [2]string{"reservation_id", reservation})
	}
	if detail.MacBootMode != nil {
		kv = append(kv, [2]string{"mac_boot_mode", *detail.MacBootMode})
	}
	return kv
}

// The display label for the artifact the sandbox booted: "image" for an image,
// "snapshot" for a sandbox resumed from a memory snapshot.
func sandboxArtifactLabel(image *gen.ImageReference, snapshot *gen.SnapshotReference) string {
	if image == nil && snapshot != nil {
		return "snapshot"
	}
	return "image"
}

func sandboxArtifactSummary(image *gen.ImageReference, snapshot *gen.SnapshotReference) string {
	if image != nil {
		if image.Name != nil && strings.TrimSpace(*image.Name) != "" {
			return *image.Name
		}
		return image.Id
	}
	if snapshot != nil {
		if snapshot.Name != nil && strings.TrimSpace(*snapshot.Name) != "" {
			return *snapshot.Name
		}
		return snapshot.Id
	}
	return ""
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
	return a.Printer().Object(resolved.Detail, sandboxDetailKV(resolved.Detail))
}
