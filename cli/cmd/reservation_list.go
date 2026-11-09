// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/spf13/cobra"
)

func newReservationListCommand() *cobra.Command {
	var flags pageFlags
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List Mac reservations",
		Long:  "List a page of the organization's Mac reservations (GET /reservations).\nExample:\n  daytona reservation list --limit 25\n  daytona reservation list --all",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			a := FromContext(cmd.Context())
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			fetch := func(ctx context.Context, cursor string) (output.List[api.Raw[gen.MacReservation]], error) {
				return getRawList[gen.MacReservation](ctx, client, "/reservations", listQuery(flags, cursor), "reservations")
			}
			var list output.List[api.Raw[gen.MacReservation]]
			if flags.all {
				list, err = collectPaged(cmd.Context(), flags.cursor, fetch)
			} else {
				list, err = fetch(cmd.Context(), flags.cursor)
			}
			if err != nil {
				return err
			}
			return listOutRaw(a.Printer(), list, reservationTable)
		},
	}
	addPageFlags(cmd, &flags)
	return cmd
}
