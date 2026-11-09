// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"strings"

	"github.com/spf13/cobra"
)

type sandboxSearchFlags struct {
	pageFlags
	namePrefix          string
	idPrefix            string
	status              []string
	classes             []string
	regions             []string
	imageRef            string
	imageRefPrefix      string
	labels              string
	spot                bool
	spotSet             bool
	forkedFrom          string
	minCores            float64
	minCoresSet         bool
	maxCores            float64
	maxCoresSet         bool
	minMemory           int64
	minMemorySet        bool
	maxMemory           int64
	maxMemorySet        bool
	minDisk             int64
	minDiskSet          bool
	maxDisk             int64
	maxDiskSet          bool
	createdStartAt      string
	createdEndAt        string
	updatedStartAt      string
	updatedEndAt        string
	lastActivityStartAt string
	lastActivityEndAt   string
	sort                string
	order               string
}

func newSandboxSearchCommand() *cobra.Command {
	flags := sandboxSearchFlags{}
	cmd := &cobra.Command{
		Use:   "search [query]",
		Short: "Search sandboxes across regions",
		Long:  "Search sandboxes across regions. The query is optional: filters alone select a set.\nExamples:\n  daytona sandbox search python\n  daytona sandbox search worker --status running --class linux --region region-a\n  daytona sandbox search --status running\n  daytona sandbox search sandbox-example",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			q := ""
			if len(args) == 1 {
				q = args[0]
			}
			flags.spotSet = cmd.Flags().Changed("spot")
			flags.minCoresSet = cmd.Flags().Changed("min-cores")
			flags.maxCoresSet = cmd.Flags().Changed("max-cores")
			flags.minMemorySet = cmd.Flags().Changed("min-memory")
			flags.maxMemorySet = cmd.Flags().Changed("max-memory")
			flags.minDiskSet = cmd.Flags().Changed("min-disk")
			flags.maxDiskSet = cmd.Flags().Changed("max-disk")
			if region := strings.TrimSpace(a.Globals.Region); region != "" {
				flags.regions = []string{region}
			}
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			var list sandboxSearchResult
			if flags.all {
				list, err = collectSandboxSearchPages(cmd.Context(), flags.cursor, func(ctx context.Context, cursor string) (sandboxSearchResult, error) {
					return fetchSandboxSearchPage(ctx, client, q, flags, cursor)
				})
			} else {
				list, err = fetchSandboxSearchPage(cmd.Context(), client, q, flags, flags.cursor)
			}
			if err != nil {
				return err
			}
			return writeSandboxSearchResult(a.Printer(), list)
		},
	}
	addPageFlags(cmd, &flags.pageFlags)
	addRegionFlag(cmd, "search only this region")
	cmd.Flags().StringVar(&flags.namePrefix, "name-prefix", "", "filter by sandbox name prefix")
	cmd.Flags().StringVar(&flags.idPrefix, "id-prefix", "", "filter by sandbox id prefix")
	cmd.Flags().StringSliceVar(&flags.status, "status", nil, "filter by status (queued|pending|assigned|booting|running|pausing|paused|stopping|stopped|deleting|deleted|failed|lost; repeatable or comma-separated)")
	cmd.Flags().StringSliceVar(&flags.classes, "class", nil, "filter by class (repeatable or comma-separated)")
	cmd.Flags().StringVar(&flags.imageRef, "image-ref", "", "filter by exact image reference")
	cmd.Flags().StringVar(&flags.imageRefPrefix, "image-ref-prefix", "", "filter by image reference prefix")
	cmd.Flags().StringVar(&flags.labels, "labels", "", "filter by labels JSON object")
	cmd.Flags().BoolVar(&flags.spot, "spot", false, "filter by spot tier")
	cmd.Flags().StringVar(&flags.forkedFrom, "forked-from", "", "filter by source sandbox id")
	cmd.Flags().Float64Var(&flags.minCores, "min-cores", 0, "minimum CPU limit in cores (1 core = 2 vCPUs; a macOS core is one Mac CPU)")
	cmd.Flags().Float64Var(&flags.maxCores, "max-cores", 0, "maximum CPU limit in cores (1 core = 2 vCPUs; a macOS core is one Mac CPU)")
	cmd.Flags().Int64Var(&flags.minMemory, "min-memory", 0, "minimum memory in MiB")
	cmd.Flags().Int64Var(&flags.maxMemory, "max-memory", 0, "maximum memory in MiB")
	cmd.Flags().Int64Var(&flags.minDisk, "min-disk", 0, "minimum disk in MiB")
	cmd.Flags().Int64Var(&flags.maxDisk, "max-disk", 0, "maximum disk in MiB")
	cmd.Flags().StringVar(&flags.createdStartAt, "created-start-at", "", "inclusive creation-time lower bound (RFC3339)")
	cmd.Flags().StringVar(&flags.createdEndAt, "created-end-at", "", "exclusive creation-time upper bound (RFC3339)")
	cmd.Flags().StringVar(&flags.updatedStartAt, "updated-start-at", "", "inclusive update-time lower bound (RFC3339)")
	cmd.Flags().StringVar(&flags.updatedEndAt, "updated-end-at", "", "exclusive update-time upper bound (RFC3339)")
	cmd.Flags().StringVar(&flags.lastActivityStartAt, "last-activity-start-at", "", "inclusive activity-time lower bound (RFC3339)")
	cmd.Flags().StringVar(&flags.lastActivityEndAt, "last-activity-end-at", "", "exclusive activity-time upper bound (RFC3339)")
	cmd.Flags().StringVar(&flags.sort, "sort", "", "sort field")
	cmd.Flags().StringVar(&flags.order, "order", "", "sort order")
	return cmd
}
