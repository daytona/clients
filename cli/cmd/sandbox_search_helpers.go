// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
)

type sandboxSearchResult struct {
	Items         []api.Raw[gen.SandboxListItem] `json:"items"`
	NextCursor    string                         `json:"next_cursor,omitempty"`
	Total         int64                          `json:"total"`
	TotalRelation string                         `json:"total_relation"`
}

func fetchSandboxSearchPage(ctx context.Context, client *api.Client, queryText string, flags sandboxSearchFlags, cursor string) (sandboxSearchResult, error) {
	params := gen.SearchSandboxesParams{Q: &queryText}
	setSearchParam(&params.NamePrefix, flags.namePrefix)
	setSearchParam(&params.IdPrefix, flags.idPrefix)
	setSearchSlice(&params.Status, flags.status)
	setSearchSlice(&params.Class, flags.classes)
	setSearchStringSlice(&params.RegionIds, flags.regions)
	setSearchParam(&params.ImageRef, flags.imageRef)
	setSearchParam(&params.ImageRefPrefix, flags.imageRefPrefix)
	setSearchParam(&params.Labels, flags.labels)
	if flags.spotSet {
		params.Spot = &flags.spot
	}
	setSearchParam(&params.ForkedFrom, flags.forkedFrom)
	setSearchFloat(&params.MinCores, flags.minCores, flags.minCoresSet)
	setSearchFloat(&params.MaxCores, flags.maxCores, flags.maxCoresSet)
	setSearchInt(&params.MinMemoryMib, flags.minMemory, flags.minMemorySet)
	setSearchInt(&params.MaxMemoryMib, flags.maxMemory, flags.maxMemorySet)
	setSearchInt(&params.MinDiskMib, flags.minDisk, flags.minDiskSet)
	setSearchInt(&params.MaxDiskMib, flags.maxDisk, flags.maxDiskSet)
	if err := setSearchTime(&params.CreatedStartAt, flags.createdStartAt, "--created-start-at"); err != nil {
		return sandboxSearchResult{}, err
	}
	if err := setSearchTime(&params.CreatedEndAt, flags.createdEndAt, "--created-end-at"); err != nil {
		return sandboxSearchResult{}, err
	}
	if err := setSearchTime(&params.UpdatedStartAt, flags.updatedStartAt, "--updated-start-at"); err != nil {
		return sandboxSearchResult{}, err
	}
	if err := setSearchTime(&params.UpdatedEndAt, flags.updatedEndAt, "--updated-end-at"); err != nil {
		return sandboxSearchResult{}, err
	}
	if err := setSearchTime(&params.LastActivityStartAt, flags.lastActivityStartAt, "--last-activity-start-at"); err != nil {
		return sandboxSearchResult{}, err
	}
	if err := setSearchTime(&params.LastActivityEndAt, flags.lastActivityEndAt, "--last-activity-end-at"); err != nil {
		return sandboxSearchResult{}, err
	}
	setSearchParam(&params.Sort, flags.sort)
	setSearchParam(&params.Order, flags.order)
	if flags.limit > 0 {
		params.Limit = &flags.limit
	}
	setSearchParam(&params.Cursor, cursor)
	query := searchQueryValues(params)
	var body json.RawMessage
	if err := client.Do(ctx, http.MethodGet, "/sandboxes/search", query, nil, &body); err != nil {
		return sandboxSearchResult{}, err
	}
	page, err := api.DecodePage[gen.SandboxListItem](body, "sandboxes")
	if err != nil {
		return sandboxSearchResult{}, fmt.Errorf("decode GET /sandboxes/search response: %w", err)
	}
	var totals struct {
		Total         int64  `json:"total"`
		TotalRelation string `json:"total_relation"`
	}
	if err := json.Unmarshal(body, &totals); err != nil {
		return sandboxSearchResult{}, fmt.Errorf("decode GET /sandboxes/search response: %w", err)
	}
	return sandboxSearchResult{Items: page.Items, NextCursor: page.NextCursor, Total: totals.Total, TotalRelation: totals.TotalRelation}, nil
}

func collectSandboxSearchPages(ctx context.Context, cursor string, fetch func(context.Context, string) (sandboxSearchResult, error)) (sandboxSearchResult, error) {
	seen := map[string]struct{}{}
	combined, err := fetch(ctx, cursor)
	if err != nil {
		return sandboxSearchResult{}, err
	}
	for combined.NextCursor != "" {
		next := combined.NextCursor
		if _, ok := seen[next]; ok {
			return sandboxSearchResult{}, cerr.New(cerr.ExitFailure, "pagination cursor repeated: %q", next)
		}
		seen[next] = struct{}{}
		page, err := fetch(ctx, next)
		if err != nil {
			return sandboxSearchResult{}, err
		}
		combined.Items = append(combined.Items, page.Items...)
		combined.NextCursor = page.NextCursor
	}
	return combined, nil
}

func writeSandboxSearchResult(p output.Printer, list sandboxSearchResult) error {
	if p.Format == output.FormatJSON || p.Format == output.FormatYAML {
		return p.Object(list, nil)
	}
	if err := listOutRaw(p, output.List[api.Raw[gen.SandboxListItem]]{Items: list.Items, NextCursor: list.NextCursor}, sandboxSearchTable); err != nil {
		return err
	}
	relation := "exact"
	if list.TotalRelation == "gte" {
		relation = "at least"
	}
	_, err := fmt.Fprintf(p.W, "\n%d matches (%s)\n", list.Total, relation)
	return err
}

func sandboxSearchTable(items []gen.SandboxListItem) output.Table {
	rows := make([][]string, 0, len(items))
	for _, item := range items {
		rows = append(rows, []string{item.Id, item.Name, string(item.Class), item.Status, item.RegionId, sandboxArtifactSummary(item.Image, item.Snapshot, item.ImageRef), formatTimePtr(item.LastActivityAt)})
	}
	return output.Table{Headers: []string{"ID", "NAME", "CLASS", "STATUS", "REGION", "ARTIFACT", "LAST_ACTIVITY"}, Rows: rows}
}

func setSearchParam[T ~string](target **T, value string) {
	if strings.TrimSpace(value) == "" {
		return
	}
	typed := T(value)
	*target = &typed
}

func setSearchSlice[T ~string](target **[]T, values []string) {
	if len(values) == 0 {
		return
	}
	typed := make([]T, len(values))
	for index, value := range values {
		typed[index] = T(value)
	}
	*target = &typed
}

func setSearchStringSlice(target **string, values []string) {
	if len(values) == 0 {
		return
	}
	joined := strings.Join(values, ",")
	*target = &joined
}

func setSearchTime(target **time.Time, value, flag string) error {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	parsed, err := time.Parse(time.RFC3339, value)
	if err != nil {
		return cerr.Usage("%s must be an RFC3339 timestamp: %v", flag, err)
	}
	*target = &parsed
	return nil
}

func setSearchInt(target **int64, value int64, changed bool) {
	if !changed {
		return
	}
	*target = &value
}

func setSearchFloat(target **float64, value float64, changed bool) {
	if !changed {
		return
	}
	*target = &value
}
