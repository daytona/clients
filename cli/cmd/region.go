// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"strconv"
	"strings"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/spf13/cobra"
)

func init() {
	Register(newRegionCommand)
}

// regionListRow is one `region list` row: the catalog row as the API sent it
// plus the price multiplier a create naming the region is billed at, or null
// when the multipliers could not be read.
type regionListRow struct {
	gen.Region
	PriceMultiplier *float64 `json:"price_multiplier"`
	raw             json.RawMessage
}

// MarshalJSON prints the API's region object with price_multiplier added.
func (r regionListRow) MarshalJSON() ([]byte, error) {
	return withField(r.raw, "price_multiplier", r.PriceMultiplier)
}

func newRegionCommand() *cobra.Command {
	cmd := &cobra.Command{Use: "region", Short: "Inspect regions", GroupID: GroupWorkload}
	list := &cobra.Command{
		Use:   "list",
		Short: "List regions",
		Long:  "List your regions, indenting each region under its parent.\nColumns are NAME, ID, PRICE MULTIPLIER and AVAILABLE.\nThe region a create that names no region goes to is marked (default); JSON and YAML name it in default_region_id (null when it is not listed).\nAVAILABLE says whether a create can name the region; a create can still fail for lack of capacity.\nPRICE MULTIPLIER is the region's rate relative to the base price. It needs a login with read:billing in the selected organization, and shows - otherwise (null in JSON and YAML).\nExample:\n  daytona region list",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			a := FromContext(cmd.Context())
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			var body json.RawMessage
			if err := client.Do(cmd.Context(), http.MethodGet, "/regions", nil, nil, &body); err != nil {
				return err
			}
			page, err := api.DecodePage[gen.Region](body, "regions")
			if err != nil {
				return fmt.Errorf("decode GET /regions response: %w", err)
			}
			var envelope struct {
				DefaultRegionID *string `json:"default_region_id"`
			}
			if err := json.Unmarshal(body, &envelope); err != nil {
				return fmt.Errorf("decode GET /regions response: %w", err)
			}
			multipliers := regionMultipliers(cmd.Context(), a)
			rows := make([]regionListRow, 0, len(page.Items))
			for _, region := range page.Items {
				rows = append(rows, regionListRow{Region: region.V, PriceMultiplier: multiplierFor(multipliers, region.V.Id), raw: region.JSON})
			}
			out := regionListOutput{List: wholeList(rows), DefaultRegionID: envelope.DefaultRegionID}
			return a.Printer().TableOf(out, regionListTable(rows, envelope.DefaultRegionID))
		},
	}
	cmd.AddCommand(list)
	return cmd
}

// regionListOutput is the list envelope plus the catalog's default region:
// the region a create that names none is sent to, or null when it is not
// listed.
type regionListOutput struct {
	output.List[regionListRow]
	DefaultRegionID *string `json:"default_region_id"`
}

func regionListTable(items []regionListRow, defaultRegionID *string) output.Table {
	table := make([][]string, 0, len(items))
	for _, item := range items {
		name := strings.Repeat("  ", len(item.ParentRegionIds)) + item.Name
		if defaultRegionID != nil && item.Id == *defaultRegionID {
			name += " (default)"
		}
		table = append(table, []string{name, item.Id, formatMultiplier(item.PriceMultiplier), strconv.FormatBool(item.Available)})
	}
	return output.Table{Headers: []string{"NAME", "ID", "PRICE MULTIPLIER", "AVAILABLE"}, Rows: table}
}

// regionMultiplierTimeout bounds the optional multiplier lookup, so a slow
// billing route never holds up a list that has already loaded.
var regionMultiplierTimeout = 5 * time.Second

// regionMultipliers reads the selected organization's region price
// multipliers, or nil when they cannot be read. The route admits only a
// login session holding read:billing, so a static credential, a missing
// organization, a refusal or a timeout leaves the column empty instead of
// failing the list.
func regionMultipliers(ctx context.Context, a *app.App) map[string]float64 {
	if a.HasExplicitCredential() || a.CredentialSource == "profile-token" || a.CredentialSource == "" {
		return nil
	}
	ctx, cancel := context.WithTimeout(ctx, regionMultiplierTimeout)
	defer cancel()
	orgID, err := a.RequireOrg(ctx, nil)
	if err != nil {
		return nil
	}
	mc, err := a.Client(ctx)
	if err != nil {
		return nil
	}
	list, err := billingGet[gen.RegionMultiplierList](ctx, mc, billingBasePath(orgID)+"/region-multipliers", nil)
	if err != nil {
		return nil
	}
	out := make(map[string]float64, len(list.RegionMultipliers))
	for _, m := range list.RegionMultipliers {
		out[m.RegionId] = m.Multiplier
	}
	return out
}

// multiplierFor is the multiplier a create naming regionID is billed at: the
// region's own entry, or the base price when no region-specific entry exists.
func multiplierFor(multipliers map[string]float64, regionID string) *float64 {
	if m, ok := multipliers[regionID]; ok {
		return &m
	}
	if m, ok := multipliers["earth"]; ok {
		return &m
	}
	return nil
}

// formatMultiplier renders 1.15 as "1.15" and 1 as "1.0": two decimals at
// most and never a bare integer, matching the dashboard, which also reads a
// non-finite multiplier as the base price.
func formatMultiplier(m *float64) string {
	if m == nil {
		return "-"
	}
	rate := *m
	if math.IsNaN(rate) || math.IsInf(rate, 0) {
		rate = 1
	}
	s := strconv.FormatFloat(rate, 'f', 2, 64)
	return strings.TrimSuffix(s, "0")
}
