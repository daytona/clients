// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"slices"
	"strconv"
	"strings"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/regions"
	"github.com/daytona/clients/cli/internal/workload"
	openapi_types "github.com/oapi-codegen/runtime/types"
	"github.com/spf13/cobra"
)

type pageFlags struct {
	limit  int64
	cursor string
	all    bool
}

// maxPageLimit is the largest page the API serves.
const maxPageLimit = 200

func addPageFlags(cmd *cobra.Command, flags *pageFlags) {
	flags.limit = 100
	cmd.Flags().Var(&limitFlag{target: &flags.limit, max: maxPageLimit}, "limit", fmt.Sprintf("max items to return, 1 to %d", maxPageLimit))
	cmd.Flags().StringVar(&flags.cursor, "cursor", "", "opaque continuation token")
	cmd.Flags().BoolVar(&flags.all, "all", false, "follow every page until exhausted")
}

// limitFlag is an integer page size validated while flags are parsed, so an
// out-of-range --limit is a usage error (exit 2) before anything is sent.
type limitFlag struct {
	target *int64
	max    int64
}

func (f *limitFlag) String() string { return strconv.FormatInt(*f.target, 10) }

func (f *limitFlag) Type() string { return "int" }

func (f *limitFlag) Set(raw string) error {
	n, err := strconv.ParseInt(strings.TrimSpace(raw), 10, 64)
	if err != nil {
		return fmt.Errorf("want an integer from 1 to %d", f.max)
	}
	if n < 1 || n > f.max {
		return fmt.Errorf("must be between 1 and %d", f.max)
	}
	*f.target = n
	return nil
}

func wholeList[T any](items []T) output.List[T] {
	return output.List[T]{Items: items}
}

func jsonCell(v any) string {
	if v == nil {
		return ""
	}
	return strings.TrimSpace(marshalJSONCell(v))
}

func marshalJSONCell(v any) string {
	b, _ := json.Marshal(v)
	return string(b)
}

func formatPtr[T ~string](value *T) string {
	if value == nil {
		return ""
	}
	return string(*value)
}

func formatTimePtr(value *time.Time) string {
	if value == nil {
		return ""
	}
	return value.Format(time.RFC3339)
}

func formatUUIDPtr(value *openapi_types.UUID) string {
	if value == nil {
		return ""
	}
	return value.String()
}

func init() {
	Register(newCPUTypeListCommand)
	Register(newMacModelListCommand)
}

func newCPUTypeListCommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:     "cpu-type",
		Short:   "Inspect the CPU type catalog",
		GroupID: GroupWorkload,
	}
	list := &cobra.Command{
		Use:   "list",
		Short: "List CPU types",
		Long: "List CPU types from the CPU catalog.\n\n" +
			"With --region, also show which types are available in that region:\n" +
			"AVAILABLE is \"exact\" when the requested type is available, \"fallback\" when a\n" +
			"higher tier of the same architecture serves it, and \"-\" when nothing does.\n" +
			"Examples:\n" +
			"  daytona cpu-type list\n" +
			"  daytona cpu-type list --region region-a",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			var slug string
			// Gate on flag presence, not on the value: `--region ""` is a usage
			// error like `--region "  "`, never a silent fall-through to the
			// unscoped catalog listing.
			if cmd.Flags().Changed("region") {
				var err error
				if slug, err = catalogRegionSlug(regionFlag(cmd)); err != nil {
					return err
				}
			}
			a := FromContext(cmd.Context())
			home, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			if slug != "" {
				client, err := catalogClient(cmd.Context(), home, workload.NewCatalogCache(), slug)
				if err != nil {
					return err
				}
				var body json.RawMessage
				query := url.Values{"region_ids": {slug}}
				if err := client.Do(cmd.Context(), http.MethodGet, "/cpu-catalog", query, nil, &body); err != nil {
					return err
				}
				resp, err := decodeRaw[gen.CpuCatalog]("GET /cpu-catalog", body)
				if err != nil {
					return err
				}
				if err := requireCatalogRegion("/cpu-catalog", cpuCatalogRegionIDs(resp), slug); err != nil {
					return err
				}
				page, err := api.DecodePage[gen.CpuType](body, "cpu_types")
				if err != nil {
					return err
				}
				return listOutRaw(a.Printer(), output.List[api.Raw[gen.CpuType]]{Items: page.Items}, func(items []gen.CpuType) output.Table { return cpuCatalogTable(items, slug) })
			}
			list, err := getRawWhole[gen.CpuType](cmd.Context(), home, "/cpu-catalog", nil, "cpu_types")
			if err != nil {
				return err
			}
			return listOutRaw(a.Printer(), list, func(items []gen.CpuType) output.Table {
				rows := make([][]string, 0, len(items))
				for _, item := range items {
					rows = append(rows, []string{item.Id, item.Name, string(item.CpuArch), strconv.Itoa(int(item.Tier))})
				}
				return output.Table{Headers: []string{"ID", "NAME", "ARCH", "TIER"}, Rows: rows}
			})
		},
	}
	addRegionFlag(list, "region slug: show availability from GET /cpu-catalog")
	cmd.AddCommand(list)
	return cmd
}

func newMacModelListCommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:     "mac-model",
		Short:   "Inspect the Mac model catalog",
		GroupID: GroupWorkload,
	}
	list := &cobra.Command{
		Use:   "list",
		Short: "List registered Mac models",
		Long: "List registered Mac models.\n" +
			"Find regions with daytona region list (GET /regions).\n" +
			"With --region, also show how many Macs of each model a purchase can reserve now (AVAILABLE) and the price of one Mac-hour and one 24-hour period.\n" +
			"Examples:\n" +
			"  daytona mac-model list\n" +
			"  daytona mac-model list --region '<region>'",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			var slug string
			// As in `cpu-type list`: `--region ""` is a usage error, never the
			// registry listing.
			if cmd.Flags().Changed("region") {
				var err error
				if slug, err = catalogRegionSlug(regionFlag(cmd)); err != nil {
					return err
				}
			}
			a := FromContext(cmd.Context())
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			if slug != "" {
				entries, regionCatalog, err := fetchMacCatalog(cmd.Context(), client, slug)
				if err != nil {
					return err
				}
				return output.ListOut(a.Printer(), wholeList(entries), func([]json.RawMessage) output.Table { return macCatalogTable(regionCatalog) })
			}
			list, err := getRawWhole[gen.MacModel](cmd.Context(), client, "/mac-models", nil, "mac_models")
			if err != nil {
				return err
			}
			return listOutRaw(a.Printer(), list, func(items []gen.MacModel) output.Table {
				rows := make([][]string, 0, len(items))
				for _, item := range items {
					rows = append(rows, macModelRow(item))
				}
				return output.Table{Headers: macModelHeaders, Rows: rows}
			})
		},
	}
	addRegionFlag(list, "region slug: also show free Macs and prices from GET /mac-catalog")
	cmd.AddCommand(list)
	return cmd
}

var macModelHeaders = []string{"ID", "NAME", "CORES", "MEMORY_MIB", "DISK_MIB", "SANDBOX_DISK_MIB", "MAX_SANDBOXES", "DEFAULT_SIZE"}

func macModelRow(item gen.MacModel) []string {
	return []string{
		item.Id, item.Name, strconv.Itoa(int(item.Cores)), strconv.Itoa(int(item.MemoryMib)), strconv.Itoa(int(item.DiskMib)),
		strconv.Itoa(int(item.SandboxDiskMib)), strconv.Itoa(int(item.MaxSandboxes)), macDefaultSizeCell(item),
	}
}

func macDefaultSizeCell(item gen.MacModel) string {
	if item.DefaultSize != nil {
		return fmt.Sprintf("%d cores / %d MiB / %d MiB disk", item.DefaultSize.Cores, item.DefaultSize.MemoryMib, item.DefaultSize.DiskMib)
	}
	return "-"
}

// macCatalogTable renders one region of `GET /mac-catalog`: the registry
// columns plus AVAILABLE and the prices, `-` when pricing is unavailable.
func macCatalogTable(region gen.MacCatalogRegion) output.Table {
	rows := make([][]string, 0, len(region.Models))
	for _, entry := range region.Models {
		perHour, perPeriod := "-", "-"
		if entry.Price != nil {
			perHour = formatCentsPerHour(float64(entry.Price.CentsPerHour))
			perPeriod = formatCents(entry.Price.PeriodCents)
		}
		rows = append(rows, append(macModelRow(entry.Model), strconv.FormatInt(int64(entry.Available), 10), perHour, perPeriod))
	}
	return output.Table{Headers: append(slices.Clone(macModelHeaders), "AVAILABLE", "PRICE_PER_HOUR", "PRICE_PER_PERIOD"), Rows: rows}
}

// fetchMacCatalog reads `GET /mac-catalog` from a regional API endpoint,
// as `cpu-type list --region` reads `GET /cpu-catalog`, and returns that
// region's model entries as received (JSON output keeps their nulls) with the
// typed region for the table.
func fetchMacCatalog(ctx context.Context, home *api.Client, slug string) ([]json.RawMessage, gen.MacCatalogRegion, error) {
	client, err := catalogClient(ctx, home, workload.NewCatalogCache(), slug)
	if err != nil {
		return nil, gen.MacCatalogRegion{}, err
	}
	var raw json.RawMessage
	if err := client.Do(ctx, http.MethodGet, "/mac-catalog", url.Values{"region_ids": {slug}}, nil, &raw); err != nil {
		return nil, gen.MacCatalogRegion{}, err
	}
	resp, err := decodeRaw[gen.MacCatalog]("GET /mac-catalog", raw)
	if err != nil {
		return nil, gen.MacCatalogRegion{}, err
	}
	var rawRegions struct {
		Regions []struct {
			ID     string            `json:"id"`
			Models []json.RawMessage `json:"models"`
		} `json:"regions"`
	}
	if err := json.Unmarshal(raw, &rawRegions); err != nil {
		return nil, gen.MacCatalogRegion{}, cerr.Wrap(cerr.ExitFailure, err, "GET /mac-catalog: decode response: %v", err)
	}
	ids := make([]string, 0, len(resp.Regions))
	for i, r := range resp.Regions {
		if r.Id == slug {
			entries := []json.RawMessage{}
			if i < len(rawRegions.Regions) && rawRegions.Regions[i].ID == slug {
				entries = append(entries, rawRegions.Regions[i].Models...)
			}
			return entries, r, nil
		}
		ids = append(ids, r.Id)
	}
	return nil, gen.MacCatalogRegion{}, requireCatalogRegion("/mac-catalog", ids, slug)
}

// decodeRaw types a response body read as json.RawMessage. Commands whose
// JSON output is the API body verbatim print the raw body and render tables
// from the typed value: re-encoding the generated type drops null fields.
func decodeRaw[T any](what string, raw json.RawMessage) (T, error) {
	var v T
	if err := json.Unmarshal(raw, &v); err != nil {
		return v, cerr.Wrap(cerr.ExitFailure, err, "%s: decode response: %v", what, err)
	}
	return v, nil
}

// formatCents prints whole cents as dollars: 1234 is `$12.34`.
func formatCents(cents int64) string {
	return fmt.Sprintf("$%d.%02d", cents/100, cents%100)
}

// formatCentsPerHour prints billing's fractional cents-per-hour rate as
// dollars without rounding it: 123.45 is `$1.2345`. It moves the decimal
// point in the rate's shortest decimal form, since dividing the float by 100
// can print digits the rate does not have.
func formatCentsPerHour(cents float64) string {
	whole, frac, _ := strings.Cut(strconv.FormatFloat(cents, 'f', -1, 64), ".")
	whole = strings.Repeat("0", max(0, 3-len(whole))) + whole
	frac = whole[len(whole)-2:] + frac
	return "$" + whole[:len(whole)-2] + "." + frac
}

// catalogRegionSlug trims and lowercases --region so the query and AVAILABLE
// comparison use the same slug. The AVAILABLE column describes one region,
// so a comma-separated list is a usage error.
func catalogRegionSlug(raw string) (string, error) {
	if strings.Contains(raw, ",") {
		return "", cerr.New(cerr.ExitUsage, "--region takes one region slug")
	}
	slug := strings.ToLower(strings.TrimSpace(raw))
	if slug == "" {
		return "", cerr.New(cerr.ExitUsage, "--region takes one region slug")
	}
	return slug, nil
}

// catalogClient picks the regional API endpoint that answers a catalog (`GET
// /cpu-catalog`, `GET /mac-catalog`) for `slug` the way sandbox routing does
// (`regions.BaseFor` over the `GET /regions` catalog): an API endpoint answers
// 409 for a region it does not serve. A region
// that publishes no `api_base_url` is served by the default API and keeps
// the home client. A slug the catalog does not list is refused here with the
// same exit code the server's 400 would carry, before any catalog request.
func catalogClient(ctx context.Context, home *api.Client, cache *workload.CatalogCache, slug string) (*api.Client, error) {
	cat, err := cache.Fetch(ctx, home)
	if err != nil {
		return nil, err
	}
	selected := cat.Find(slug)
	if selected == nil {
		return nil, cerr.New(cerr.ExitUsage, "unknown region %q (see `daytona region list`)", slug)
	}
	return home.WithBaseURL(regions.BaseFor(*selected, home.BaseURL)), nil
}

func cpuCatalogRegionIDs(resp gen.CpuCatalog) []string {
	ids := make([]string, 0, len(resp.Regions))
	for _, r := range resp.Regions {
		ids = append(ids, r.Id)
	}
	return ids
}

// requireCatalogRegion refuses a catalog answer whose regions (`ids`) do not
// include `slug`. The server rejects an unknown or unhosted region before
// answering, so this only fires on a server defect — but a 200 for some
// other region would otherwise render every row as `-` and look like a true
// answer.
func requireCatalogRegion(path string, ids []string, slug string) error {
	if slices.Contains(ids, slug) {
		return nil
	}
	answered := "no regions"
	if len(ids) > 0 {
		answered = strings.Join(ids, ", ")
	}
	return cerr.New(cerr.ExitFailure, "GET %s did not answer for region %s (it covered %s)", path, slug, answered)
}

// cpuCatalogTable renders `GET /cpu-catalog` for one region: the four
// registry columns plus AVAILABLE — `exact` when the type is available in `region`,
// `fallback` when a higher same-arch tier serves it, `-`
// otherwise.
func cpuCatalogTable(items []gen.CpuType, region string) output.Table {
	rows := make([][]string, 0, len(items))
	for _, item := range items {
		rows = append(rows, []string{item.Id, item.Name, string(item.CpuArch), strconv.Itoa(int(item.Tier)), cpuAvailability(item, region)})
	}
	return output.Table{Headers: []string{"ID", "NAME", "ARCH", "TIER", "AVAILABLE"}, Rows: rows}
}

func cpuAvailability(item gen.CpuType, region string) string {
	if slices.Contains(item.ExactRegionIds, region) {
		return "exact"
	}
	if slices.Contains(item.FallbackRegionIds, region) {
		return "fallback"
	}
	return "-"
}

func listQuery(flags pageFlags, cursor string) url.Values {
	query := url.Values{}
	if flags.limit > 0 {
		query.Set("limit", strconv.FormatInt(flags.limit, 10))
	}
	if cursor != "" {
		query.Set("cursor", cursor)
	}
	return query
}

// regionFlag is the --region value as given.
func regionFlag(cmd *cobra.Command) string {
	f := cmd.Flags().Lookup("region")
	if f == nil {
		return ""
	}
	return f.Value.String()
}
