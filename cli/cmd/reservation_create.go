// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"slices"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/input"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/google/uuid"
	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
)

func newReservationCreateCommand() *cobra.Command {
	var model, idempotencyKey string
	var count, minCount int
	var autoRenew, dryRun bool
	cmd := &cobra.Command{
		Use:   "create",
		Short: "Reserve a Mac",
		Long: "Reserve a Mac of one model in a region (POST /reservations). The SDK twin is reservations.create(…).\n" +
			"Find regions with daytona region list (GET /regions), and models and prices with daytona mac-model list --region '<region>'.\n" +
			"With --count, reserve several Macs of one model in one request (POST /reservations/bulk). The SDK twin is reservations.createBulk(…).\n" +
			"Without --model the region's default model is reserved; an unavailable model is never upgraded to another one.\n" +
			"Without --idempotency-key the CLI sends a fresh key; pass your own to retry without buying a second Mac.\n" +
			"Purchases require confirmation; use --yes to consent without a prompt. --dry-run reserves nothing and needs no confirmation.\n" +
			"Examples:\n" +
			"  " + reservationCreateExample + "\n" +
			"  " + reservationBulkExample + "\n" +
			"  " + reservationMinCountExample + "\n" +
			"  " + reservationDryRunExample,
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			a := FromContext(cmd.Context())
			flags := cmd.Flags()
			if err := validateReservationCreateFlags(flags, count, minCount); err != nil {
				return err
			}
			body, err := input.Load(a.Globals.File, os.Stdin, false)
			if err != nil {
				return err
			}
			region, err := reservationRegion(a, body)
			if err != nil {
				return err
			}
			body.Set("region_id", region)
			workload.SetIfChanged(body, flags, "model", "model", model)
			workload.SetIfChanged(body, flags, "auto-renew", "auto_renew", autoRenew)
			if !dryRun {
				quantity := 1
				if flags.Changed("count") {
					quantity = count
				}
				if err := a.Confirm("buy", fmt.Sprintf("%d 24-hour Mac reservation(s) in %s billed to the organization", quantity, region)); err != nil {
					return err
				}
			}
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			if dryRun {
				if !flags.Changed("count") {
					count = 1
				}
				return reservationDryRun(cmd, a, client, body, region, count)
			}
			if !flags.Changed("idempotency-key") {
				idempotencyKey = uuid.NewString()
			}
			headers := http.Header{"Idempotency-Key": {idempotencyKey}}
			if flags.Changed("count") {
				workload.SetIfChanged(body, flags, "count", "count", count)
				workload.SetIfChanged(body, flags, "min-count", "min_count", minCount)
				req, err := workload.DecodeBody[gen.CreateReservationsBulkRequest](body)
				if err != nil {
					return err
				}
				var raw json.RawMessage
				if err := client.DoWithHeaders(cmd.Context(), http.MethodPost, "/reservations/bulk", nil, headers, req, &raw); err != nil {
					return err
				}
				batch, err := decodeRaw[gen.MacReservationBatch]("POST /reservations/bulk", raw)
				if err != nil {
					return err
				}
				if err := a.Printer().TableOf(raw, reservationTable(batch.Reservations)); err != nil {
					return err
				}
				_, err = fmt.Fprintf(a.Stderr, "reserved %d of %d Macs\n", batch.Reserved, batch.Requested)
				return err
			}
			req, err := workload.DecodeBody[gen.CreateReservationRequest](body)
			if err != nil {
				return err
			}
			var reservation api.Raw[gen.MacReservation]
			if err := client.DoWithHeaders(cmd.Context(), http.MethodPost, "/reservations", nil, headers, req, &reservation); err != nil {
				return err
			}
			return a.Printer().Object(reservation, reservationKV(reservation.V))
		},
	}
	f := cmd.Flags()
	f.StringVar(&model, "model", "", "Mac model slug from mac-model list (default: the region's default model; no upgrade)")
	f.IntVar(&count, "count", 0, "how many Macs to reserve, 1 to 100 (calls POST /reservations/bulk; default: one Mac through POST /reservations)")
	f.IntVar(&minCount, "min-count", 0, "the fewest Macs to accept when fewer than --count are free or fit under the organization's Mac reservation limit (default: --count, all or nothing)")
	f.BoolVar(&autoRenew, "auto-renew", false, autoRenewUsage)
	f.StringVar(&idempotencyKey, "idempotency-key", "", "a retry with the same key returns the first reservation instead of buying another (default: a fresh key per invocation)")
	f.BoolVar(&dryRun, "dry-run", false, "print the price and the free Macs from GET /mac-catalog and reserve nothing")
	flagPlaceholder(f, "model", "MODEL")
	flagPlaceholder(f, "count", "N")
	flagPlaceholder(f, "min-count", "M")
	flagPlaceholder(f, "idempotency-key", "KEY")
	addRegionFlag(cmd, "region whose Macs to reserve (required unless region_id is in --file)")
	addFileFlag(cmd, "")
	return cmd
}

// validateReservationCreateFlags refuses flag values the API would answer
// with a 400, before any request. --min-count alone is refused rather than
// read as a bulk purchase of one Mac: the floor only means something below a
// --count.
func validateReservationCreateFlags(flags *pflag.FlagSet, count, minCount int) error {
	if flags.Changed("min-count") && !flags.Changed("count") {
		return cerr.Usage("--min-count needs --count; example: " + reservationMinCountExample)
	}
	if flags.Changed("count") && (count < 1 || count > maxReservationCount) {
		return cerr.Usage("--count must be between 1 and 100; example: daytona reservation create --region '<region>' --count 3")
	}
	if flags.Changed("min-count") && (minCount < 1 || minCount > count) {
		return cerr.Usage("--min-count must be between 1 and --count (%d); example: %s", count, reservationMinCountExample)
	}
	return nil
}

// reservationDryRunResult is `reservation create --dry-run`'s JSON: what the
// purchase would buy and cost, from the region's GET /mac-catalog. The price
// fields are null when pricing is unavailable.
type reservationDryRunResult struct {
	Region      string `json:"region"`
	Model       string `json:"model"`
	Count       int    `json:"count"`
	Available   int64  `json:"available"`
	PeriodCents *int64 `json:"period_cents"`
	TotalCents  *int64 `json:"total_cents"`
}

// reservationDryRun answers `--dry-run` from the regional API endpoint, reached the
// way `mac-model list --region` reaches it. An omitted model uses the region's default.
func reservationDryRun(cmd *cobra.Command, a *app.App, home *api.Client, body input.Body, region string, count int) error {
	slug := strings.ToLower(region)
	_, regionCatalog, err := fetchMacCatalog(cmd.Context(), home, slug)
	if err != nil {
		return err
	}
	modelID := regionCatalog.DefaultModel
	if requested, ok := body["model"].(string); ok && strings.TrimSpace(requested) != "" {
		modelID = strings.TrimSpace(requested)
	}
	at := slices.IndexFunc(regionCatalog.Models, func(m gen.MacCatalogModel) bool { return m.Model.Id == modelID })
	if at < 0 {
		return cerr.Usage("unknown Mac model %q in region %s (see `daytona mac-model list --region %s`)", modelID, slug, slug)
	}
	entry := regionCatalog.Models[at]
	result := reservationDryRunResult{Region: slug, Model: modelID, Count: count, Available: int64(entry.Available)}
	text := fmt.Sprintf("would reserve %d x %s in %s: no price in this region; %d available now", count, modelID, slug, result.Available)
	if entry.Price != nil {
		period := entry.Price.PeriodCents
		total := period * int64(count)
		result.PeriodCents, result.TotalCents = &period, &total
		text = fmt.Sprintf("would reserve %d x %s in %s: %s per Mac per %d h, %s for the first period; %d available now",
			count, modelID, slug, formatCents(period), entry.Price.PeriodSeconds/3600, formatCents(total), result.Available)
	}
	return a.Printer().Raw(text, result)
}

// reservationRegion resolves the purchase region: the --region flag, then the
// --file body. The region is required and has no server
// default, so a missing one is a usage error before any request.
func reservationRegion(a *app.App, body input.Body) (string, error) {
	region := strings.TrimSpace(a.Globals.Region)
	if region == "" {
		if fromFile, ok := body["region_id"].(string); ok {
			region = strings.TrimSpace(fromFile)
		}
	}
	if region == "" {
		return "", cerr.Usage("reservation create needs --region (the region whose Macs to reserve); example: daytona reservation create --region '<region>'")
	}
	return region, nil
}
