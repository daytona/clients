// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/mgmt"
	"github.com/spf13/cobra"
)

func init() {
	Register(newServiceAccountCmd)
	extendProjectCmd(newProjectServiceAccountCmd)
}

func newServiceAccountCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "service-account", Short: "Service accounts: create, list, get, update, delete", GroupID: GroupManagement}
	cmd.AddCommand(newServiceAccountCreateCmd(), newServiceAccountListCmd(), newServiceAccountGetCmd(), newServiceAccountUpdateCmd(), newServiceAccountDeleteCmd())
	return cmd
}

func newServiceAccountCreateCmd() *cobra.Command {
	var roleCSV string
	cmd := &cobra.Command{Use: "create <name>", Short: "Create a service account", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		roles, err := splitCSV(roleCSV, "--role")
		if err != nil {
			return err
		}
		a := FromContext(cmd.Context())
		ctx := cmd.Context()
		orgID, err := a.RequireOrg(ctx, pickOrg(a))
		if err != nil {
			return err
		}
		mc, err := a.Client(ctx)
		if err != nil {
			return err
		}
		resp, err := mgmt.CreateServiceAccount(ctx, mc, orgID, gen.CreateServiceAccountRequest{Name: args[0], Roles: roles})
		if err != nil {
			return err
		}
		return a.Printer().Object(resp, [][2]string{{"id", resp.V.Id.String()}, {"name", resp.V.Name}, {"roles", joinComma(resp.V.Roles)}, {"permissions", joinComma(resp.V.OrgPermissions)}})
	}}
	addCSVListFlag(cmd.Flags(), &roleCSV, "role", "role slug (repeatable or comma-separated)")
	return cmd
}

func newServiceAccountListCmd() *cobra.Command {
	var flags pageFlags
	cmd := &cobra.Command{Use: "list", Short: "List service accounts in the selected organization", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		a := FromContext(cmd.Context())
		ctx := cmd.Context()
		orgID, err := a.RequireOrg(ctx, pickOrg(a))
		if err != nil {
			return err
		}
		mc, err := a.Client(ctx)
		if err != nil {
			return err
		}
		items, err := mgmtListPage(ctx, flags, func(ctx context.Context, cursor string, limit int64) (mgmt.Page[gen.OrganizationServiceAccount], error) {
			return mgmt.ServiceAccountsPage(ctx, mc, orgID, cursor, limit)
		})
		if err != nil {
			return err
		}
		return listOutRaw(a.Printer(), items, serviceAccountTable)
	}}
	addPageFlags(cmd, &flags)
	return cmd
}

func newServiceAccountGetCmd() *cobra.Command {
	return &cobra.Command{Use: "get <id>", Short: "Show one service account", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		a := FromContext(cmd.Context())
		orgID, err := a.RequireOrg(cmd.Context(), pickOrg(a))
		if err != nil {
			return err
		}
		mc, err := a.Client(cmd.Context())
		if err != nil {
			return err
		}
		resp, err := mgmt.GetServiceAccount(cmd.Context(), mc, orgID, args[0])
		if err != nil {
			return err
		}
		return a.Printer().Object(resp, [][2]string{{"id", resp.V.Id.String()}, {"name", resp.V.Name}, {"roles", joinComma(resp.V.Roles)}, {"permissions", joinComma(resp.V.OrgPermissions)}, {"live_key_count", toString(resp.V.LiveKeyCount)}})
	}}
}

func newServiceAccountUpdateCmd() *cobra.Command {
	var name, roleCSV string
	cmd := &cobra.Command{Use: "update <id>", Short: "Update a service account's name and/or roles", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		if !cmd.Flags().Changed("name") && !cmd.Flags().Changed("role") {
			return cerr.Usage("at least one of --name or --role is required")
		}
		if cmd.Flags().Changed("name") && strings.TrimSpace(name) == "" {
			return cerr.Usage("--name must not be empty")
		}
		var roles []string
		var err error
		if cmd.Flags().Changed("role") {
			roles, err = splitCSV(roleCSV, "--role")
			if err != nil {
				return err
			}
		}
		a := FromContext(cmd.Context())
		orgID, err := a.RequireOrg(cmd.Context(), pickOrg(a))
		if err != nil {
			return err
		}
		mc, err := a.Client(cmd.Context())
		if err != nil {
			return err
		}
		req := gen.UpdateServiceAccountRequest{Name: stringPtr(name)}
		if cmd.Flags().Changed("role") {
			req.Roles = mgmt.Ref(roles)
		}
		resp, err := mgmt.UpdateServiceAccount(cmd.Context(), mc, orgID, args[0], req)
		if err != nil {
			return err
		}
		return a.Printer().Object(resp, [][2]string{{"id", resp.V.Id.String()}, {"name", resp.V.Name}, {"roles", joinComma(resp.V.Roles)}, {"permissions", joinComma(resp.V.OrgPermissions)}})
	}}
	cmd.Flags().StringVar(&name, "name", "", "new service-account name")
	addCSVListFlag(cmd.Flags(), &roleCSV, "role", "role slug (repeatable or comma-separated)")
	return cmd
}

func newServiceAccountDeleteCmd() *cobra.Command {
	return &cobra.Command{Use: "delete <id>", Short: "Archive a service account and revoke its live keys", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		a := FromContext(cmd.Context())
		if err := a.Confirm("delete", "service account "+args[0]); err != nil {
			return err
		}
		orgID, err := a.RequireOrg(cmd.Context(), pickOrg(a))
		if err != nil {
			return err
		}
		mc, err := a.Client(cmd.Context())
		if err != nil {
			return err
		}
		if err := mgmt.DeleteServiceAccount(cmd.Context(), mc, orgID, args[0]); err != nil {
			return err
		}
		return printAck(a, "id", args[0], "archived", "")
	}}
}
