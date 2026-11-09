// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/mgmt"
	"github.com/spf13/cobra"
)

func newProjectServiceAccountCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "service-account", Short: "Project service-account membership: list, add, remove"}
	cmd.AddCommand(newProjectServiceAccountListCmd(), newProjectServiceAccountAddCmd(), newProjectServiceAccountRemoveCmd())
	return cmd
}

func newProjectServiceAccountListCmd() *cobra.Command {
	var flags pageFlags
	cmd := &cobra.Command{Use: "list", Short: "List service accounts seated in the selected project", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		a := FromContext(cmd.Context())
		ctx := cmd.Context()
		projectID, err := a.RequireProject(ctx, pickProject(a))
		if err != nil {
			return err
		}
		mc, err := a.Client(ctx)
		if err != nil {
			return err
		}
		items, err := mgmtListPage(ctx, flags, func(ctx context.Context, cursor string, limit int64) (mgmt.Page[gen.ProjectServiceAccount], error) {
			return mgmt.ProjectServiceAccountsPage(ctx, mc, projectID, cursor, limit)
		})
		if err != nil {
			return err
		}
		return listOutRaw(a.Printer(), items, projectServiceAccountTable)
	}}
	addPageFlags(cmd, &flags)
	return cmd
}

func newProjectServiceAccountAddCmd() *cobra.Command {
	return &cobra.Command{Use: "add <service-account>", Short: "Seat a service account in the selected project", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		a := FromContext(cmd.Context())
		ctx := cmd.Context()
		orgID, err := a.RequireOrg(ctx, pickOrg(a))
		if err != nil {
			return err
		}
		projectID, err := a.RequireProject(ctx, pickProject(a))
		if err != nil {
			return err
		}
		mc, err := a.Client(ctx)
		if err != nil {
			return err
		}
		items, err := mgmt.ListServiceAccounts(ctx, mc, orgID)
		if err != nil {
			return err
		}
		// An archived account cannot be seated (the API answers 409), so a
		// reused name resolves to the live account alone.
		acct, err := resolveServiceAccountRefForOperation(items, args[0], false)
		if err != nil {
			return err
		}
		resp, err := mgmt.AddProjectServiceAccount(ctx, mc, projectID, *acct)
		if err != nil {
			return err
		}
		return a.Printer().Object(resp, [][2]string{{"added_service_account_ids", joinComma(uuidStrings(resp.V.AddedServiceAccountIds))}})
	}}
}

func newProjectServiceAccountRemoveCmd() *cobra.Command {
	return &cobra.Command{Use: "remove <service-account>", Short: "Unseat a service account from the selected project", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		a := FromContext(cmd.Context())
		ctx := cmd.Context()
		if err := a.PreflightConfirm("remove", "project service account "+args[0]); err != nil {
			return err
		}
		orgID, err := a.RequireOrg(ctx, pickOrg(a))
		if err != nil {
			return err
		}
		projectID, err := a.RequireProject(ctx, pickProject(a))
		if err != nil {
			return err
		}
		mc, err := a.Client(ctx)
		if err != nil {
			return err
		}
		items, err := mgmt.ListServiceAccounts(ctx, mc, orgID)
		if err != nil {
			return err
		}
		acct, err := resolveServiceAccountRefForOperation(items, args[0], true)
		if err != nil {
			return err
		}
		if err := a.Confirm("remove", "project service account "+args[0]); err != nil {
			return err
		}
		if err := mgmt.RemoveProjectServiceAccount(ctx, mc, projectID, acct.Id.String()); err != nil {
			return err
		}
		return printAck(a, "id", acct.Id.String(), "removed", "")
	}}
}
