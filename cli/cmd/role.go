// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/spf13/cobra"
)

func init() {
	Register(newRoleCmd)
}

func newRoleCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "role", Short: "Inspect the organization role catalog", GroupID: GroupManagement}
	cmd.AddCommand(&cobra.Command{Use: "list", Short: "List available roles", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		a := FromContext(cmd.Context())
		mc, err := a.Client(cmd.Context())
		if err != nil {
			return err
		}
		catalog, err := getRawWhole[gen.Role](cmd.Context(), mc, "/roles", nil, "roles")
		if err != nil {
			return err
		}
		return listOutRaw(a.Printer(), catalog, func(items []gen.Role) output.Table {
			rows := make([][]string, 0, len(items))
			for _, item := range items {
				rows = append(rows, []string{item.Id, item.Name, joinComma(item.Permissions), item.Description})
			}
			return output.Table{Headers: []string{"ID", "NAME", "PERMISSIONS", "DESCRIPTION"}, Rows: rows}
		})
	}})
	return cmd
}
