// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"strings"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/regions"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

type imageListFilters struct {
	anonymous string
	platform  string
	name      string
	sandboxID string
}

func newImageListCommand() *cobra.Command {
	var flags struct {
		pageFlags
		imageListFilters
	}
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List images",
		Long:  "List images.\n--platform true lists the platform images every project can boot; --platform any adds them to the project's own.\nExamples:\n  daytona image list --limit 20\n  daytona image list --platform true",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			a := FromContext(cmd.Context())
			anonymous, err := parseImageAnonymousFilter(flags.anonymous)
			if err != nil {
				return err
			}
			flags.anonymous = anonymous
			if cmd.Flags().Changed("platform") {
				switch flags.platform {
				case "true", "false", "any":
				default:
					return cerr.Usage("--platform must be true, false or any")
				}
			}
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			source := regionalListSource{home: client, catalog: workload.NewCatalogCache(), region: a.Globals.Region, raws: newRawIndex()}
			fetch := func(ctx context.Context, cursor string) (output.List[gen.ImageListItem], error) {
				return source.fetchImagePage(ctx, listPageRequest{limit: flags.limit, cursor: cursor}, flags.imageListFilters)
			}
			var list output.List[gen.ImageListItem]
			if flags.all {
				list, err = collectPaged(cmd.Context(), flags.cursor, fetch)
			} else {
				list, err = fetch(cmd.Context(), flags.cursor)
			}
			if err != nil {
				return err
			}
			list.Items = regions.Unique(list.Items, func(item gen.ImageListItem) string { return item.Id })
			inRegion, err := source.regionFilter(cmd.Context())
			if err != nil {
				return err
			}
			list.Items = keepItems(list.Items, func(item gen.ImageListItem) bool {
				if inRegion(item.RegionId) {
					return true
				}
				for _, id := range item.RegionIds {
					if inRegion(id) {
						return true
					}
				}
				return false
			})
			if err := listOutRaw(a.Printer(), rawList(source.raws, list), func(items []gen.ImageListItem) output.Table {
				rows := make([][]string, 0, len(items))
				for _, item := range items {
					rows = append(rows, []string{item.Id, formatPtr(item.Name), item.Status, item.Origin, string(item.Retention), formatTimePtr(item.ExpiresAt), item.RegionId, strings.Join(item.RegionIds, ","), item.CreatedAt.Format(time.RFC3339)})
				}
				return output.Table{Headers: []string{"ID", "NAME", "STATUS", "ORIGIN", "RETENTION", "EXPIRES_AT", "REGION", "AVAILABLE_IN", "CREATED"}, Rows: rows}
			}); err != nil {
				return err
			}
			return regions.PartialError(list)
		},
	}
	addPageFlags(cmd, &flags.pageFlags)
	addRegionFlag(cmd, "list only the images available in this region or a region inside it (region_id or region_ids; default: every region); a page can then hold fewer than --limit items")
	cmd.Flags().StringVar(&flags.name, "name", "", "filter by exact image name")
	cmd.Flags().StringVar(&flags.anonymous, "anonymous", "", "true lists only anonymous images, false only named ones (the default)")
	cmd.Flags().StringVar(&flags.platform, "platform", "", "platform images: true (only platform images)|false (only the project's, the default)|any (both)")
	cmd.Flags().StringVar(&flags.sandboxID, "sandbox", "", "filter captures by source sandbox id")
	return cmd
}

func imageKV(image gen.Image) [][2]string {
	return [][2]string{{"id", image.Id}, {"name", formatPtr(image.Name)}, {"status", image.Status}, {"origin", image.Origin}, {"retention", string(image.Retention)}, {"expires_at", formatTimePtr(image.ExpiresAt)}, {"class", image.Class}, {"region", image.RegionId}, {"available_in", strings.Join(image.RegionIds, ",")}, {"source", jsonCell(image.Source)}}
}

// parseImageAnonymousFilter normalizes --anonymous to the API's boolean. The
// API has no "both" value (omitting it means false), so anything else is a
// usage error rather than a request the API rejects or misreads.
func parseImageAnonymousFilter(value string) (string, error) {
	v := strings.ToLower(strings.TrimSpace(value))
	switch v {
	case "", "true", "false":
		return v, nil
	}
	return "", cerr.Usage("--anonymous wants true or false, got %q", value).
		WithHint("omit it to list named images; run the list once with --anonymous true for anonymous ones")
}
