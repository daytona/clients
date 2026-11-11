// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"net/url"
	"strconv"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/google/uuid"
	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
)

func init() {
	Register(newReservationCommand)
}

// Help texts shared by `reservation create` and `sandbox create-macos`.
const autoRenewUsage = "renew while a sandbox is active; create-macos requires --reservation new"

const (
	reservationCreateExample   = "daytona reservation create --region '<region>' --model '<model>' --idempotency-key nightly-mac"
	reservationBulkExample     = "daytona reservation create --region '<region>' --model '<model>' --count 3 --idempotency-key nightly-macs"
	reservationMinCountExample = "daytona reservation create --region '<region>' --count 5 --min-count 2"
	reservationDryRunExample   = "daytona reservation create --region '<region>' --model '<model>' --count 3 --dry-run"
	reservationUpdateExample   = "daytona reservation update RESERVATION_ID --auto-renew=false"
)

// maxReservationCount is the most Macs one POST /reservations/bulk buys.
const maxReservationCount = 100

func newReservationCommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "reservation",
		Short: "Reserve dedicated Macs for macOS sandboxes",
		Long: "Reserve dedicated Macs for macOS sandboxes. A reservation is one physical Mac of a named model, paid per 24-hour period. Set sandbox sizes when creating each sandbox.\nFind regions with daytona region list (GET /regions), and models and prices with daytona mac-model list --region '<region>'.\nExamples:\n" +
			"  daytona mac-model list\n" +
			"  " + reservationCreateExample + "\n" +
			"  daytona reservation create --region '<region>' --count 3\n" +
			"  daytona reservation get RESERVATION_ID\n" +
			"  daytona reservation list\n" +
			"  daytona reservation renew RESERVATION_ID\n" +
			"  " + reservationUpdateExample,
		GroupID: GroupWorkload,
	}
	cmd.AddCommand(newReservationCreateCommand(), newReservationGetCommand(), newReservationListCommand(), newReservationRenewCommand(), newReservationUpdateCommand())
	return cmd
}

func newReservationGetCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "get ID",
		Short: "Get one Mac reservation",
		Long:  "Get one Mac reservation (GET /reservations/{id}).\nExample:\n  daytona reservation get RESERVATION_ID",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			var reservation api.Raw[gen.MacReservation]
			if err := client.Do(cmd.Context(), http.MethodGet, "/reservations/"+url.PathEscape(args[0]), nil, nil, &reservation); err != nil {
				return err
			}
			return a.Printer().Object(reservation, reservationKV(reservation.V))
		},
	}
}

func newReservationRenewCommand() *cobra.Command {
	var idempotencyKey string
	cmd := &cobra.Command{
		Use:   "renew ID",
		Short: "Charge and renew a Mac reservation for another period",
		Long: "Charge the next period of an active reservation (POST /reservations/{id}/renew); period_end moves out by one period on the same Mac. The SDK twin is reservations.renew(…).\n" +
			"Asks for confirmation at a terminal; off a terminal pass --yes (or set DAYTONA_YES=1).\n" +
			"Without --idempotency-key the CLI sends a fresh key; pass your own to retry without paying for a second period.\n" +
			"Example:\n  daytona reservation renew RESERVATION_ID --idempotency-key renew-RESERVATION_ID-2 --yes",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			// Consent comes before anything else: resolving the client can
			// exchange a token or mint a project key.
			if err := a.Confirm("renew", "reservation "+args[0]+" for another period, billed to the organization"); err != nil {
				return err
			}
			if !cmd.Flags().Changed("idempotency-key") {
				idempotencyKey = uuid.NewString()
			}
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			var reservation api.Raw[gen.MacReservation]
			headers := http.Header{"Idempotency-Key": {idempotencyKey}}
			if err := client.DoWithHeaders(cmd.Context(), http.MethodPost, "/reservations/"+url.PathEscape(args[0])+"/renew", nil, headers, nil, &reservation); err != nil {
				return err
			}
			return a.Printer().Object(reservation, reservationKV(reservation.V))
		},
	}
	cmd.Flags().StringVar(&idempotencyKey, "idempotency-key", "", "a retry with the same key returns the reservation without charging another period (default: a fresh key per invocation)")
	flagPlaceholder(cmd.Flags(), "idempotency-key", "KEY")
	return cmd
}

func newReservationUpdateCommand() *cobra.Command {
	var autoRenew bool
	cmd := &cobra.Command{
		Use:   "update ID",
		Short: "Turn a Mac reservation's auto-renew on or off",
		Long: "Turn auto-renew on or off (PATCH /reservations/{id}). An auto-renewing reservation renews near period_end only while a sandbox is in use on it; stopped and paused sandboxes do not renew it. With auto-renew off, the reservation ends at period_end.\n" +
			"Enabling auto-renew commits future charges: asks for confirmation at a terminal; off a terminal pass --yes (or set DAYTONA_YES=1). Disabling it needs no confirmation.\n" +
			"Examples:\n" +
			"  " + reservationUpdateExample + "\n" +
			"  daytona reservation update RESERVATION_ID --auto-renew=true",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if !cmd.Flags().Changed("auto-renew") {
				return cerr.Usage("reservation update needs --auto-renew=true or --auto-renew=false; example: " + reservationUpdateExample)
			}
			a := FromContext(cmd.Context())
			if autoRenew {
				if err := a.Confirm("enable auto-renew", "reservation "+args[0]+", with future periods billed to the organization"); err != nil {
					return err
				}
			}
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			var reservation api.Raw[gen.MacReservation]
			req := gen.UpdateReservationRequest{AutoRenew: autoRenew}
			if err := client.Do(cmd.Context(), http.MethodPatch, "/reservations/"+url.PathEscape(args[0]), nil, req, &reservation); err != nil {
				return err
			}
			return a.Printer().Object(reservation, reservationKV(reservation.V))
		},
	}
	cmd.Flags().BoolVar(&autoRenew, "auto-renew", false, "renew near period_end while a sandbox is in use on the reservation; false lets it end at period_end")
	return cmd
}

// reservationTable is the `reservation list` table, which a bulk purchase
// prints too.
func reservationTable(items []gen.MacReservation) output.Table {
	rows := make([][]string, 0, len(items))
	for _, item := range items {
		usage := reservationUsage(item)
		rows = append(rows, []string{item.Id, item.RegionId, usage.model, string(item.Status), usage.coresUsed, usage.coresTotal, usage.memUsed, usage.memTotal, usage.diskUsed, usage.diskTotal, strconv.FormatBool(item.AutoRenew), formatTimePtr(item.PeriodEnd)})
	}
	return output.Table{Headers: []string{"ID", "REGION", "MODEL", "STATUS", "CORES_USED", "CORES_TOTAL", "MEMORY_MIB_USED", "MEMORY_MIB_TOTAL", "DISK_MIB_USED", "DISK_MIB_TOTAL", "AUTO_RENEW", "PERIOD_END"}, Rows: rows}
}

// reservationColumns is a reservation's model and CPU/memory/disk columns as printed.
// Unavailable model and capacity values print `-`.
type reservationColumns struct {
	model, coresUsed, coresTotal, memUsed, memTotal, diskUsed, diskTotal string
}

func reservationUsage(reservation gen.MacReservation) reservationColumns {
	usage := reservationColumns{
		model:      "-",
		coresUsed:  strconv.Itoa(int(reservation.ResourcesUsed.Cores)),
		coresTotal: "-",
		memUsed:    strconv.Itoa(int(reservation.ResourcesUsed.MemoryMib)),
		memTotal:   "-",
		diskUsed:   strconv.Itoa(int(reservation.ResourcesUsed.DiskMib)),
		diskTotal:  "-",
	}
	if reservation.Model != nil && *reservation.Model != "" {
		usage.model = *reservation.Model
	}
	if reservation.Capacity != nil {
		usage.coresTotal = strconv.Itoa(int(reservation.Capacity.Cores))
		usage.memTotal = strconv.Itoa(int(reservation.Capacity.MemoryMib))
		usage.diskTotal = strconv.Itoa(int(reservation.Capacity.DiskMib))
	}
	return usage
}

func reservationKV(reservation gen.MacReservation) [][2]string {
	usage := reservationUsage(reservation)
	return [][2]string{
		{"id", reservation.Id},
		{"region", reservation.RegionId},
		{"model", usage.model},
		{"status", string(reservation.Status)},
		{"origin", string(reservation.Origin)},
		{"cores_used", usage.coresUsed},
		{"cores_total", usage.coresTotal},
		{"memory_mib_used", usage.memUsed},
		{"memory_mib_total", usage.memTotal},
		{"disk_mib_used", usage.diskUsed},
		{"disk_mib_total", usage.diskTotal},
		{"slots_used", strconv.FormatInt(reservation.SlotsUsed, 10)},
		{"auto_renew", strconv.FormatBool(reservation.AutoRenew)},
		{"period_end", formatTimePtr(reservation.PeriodEnd)},
		{"renewal_error", formatPtr(reservation.RenewalError)},
	}
}

// placeholderValue names a flag's value in help (`--cores N`): pflag prints
// Value.Type() as the placeholder when the usage text has no back-quoted name.
type placeholderValue struct {
	pflag.Value
	name string
}

func (v placeholderValue) Type() string { return v.name }

func flagPlaceholder(f *pflag.FlagSet, flag, name string) {
	registered := f.Lookup(flag)
	registered.Value = placeholderValue{Value: registered.Value, name: name}
}
