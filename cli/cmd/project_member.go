// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/mgmt"
	"github.com/spf13/cobra"
)

func init() {
	extendProjectCmd(newProjectMemberSubcmd)
}

func extendProjectCmd(add func() *cobra.Command) {
	for i, reg := range registry {
		current := reg
		built := current()
		if built != nil && built.Use == "project" {
			registry[i] = func() *cobra.Command {
				cmd := current()
				cmd.AddCommand(add())
				return cmd
			}
			return
		}
	}
}

func newProjectMemberSubcmd() *cobra.Command {
	cmd := &cobra.Command{Use: "member", Short: "Project membership: list, add, remove"}
	cmd.AddCommand(newProjectMemberListCmd(), newProjectMemberAddCmd(), newProjectMemberRemoveCmd())
	return cmd
}

func newProjectMemberListCmd() *cobra.Command {
	var flags pageFlags
	cmd := &cobra.Command{Use: "list", Short: "List members who can access the selected project", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
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
		items, err := mgmtListPage(ctx, flags, func(ctx context.Context, cursor string, limit int64) (mgmt.Page[gen.ProjectMember], error) {
			return mgmt.ProjectMembersPage(ctx, mc, projectID, cursor, limit)
		})
		if err != nil {
			return err
		}
		return listOutRaw(a.Printer(), items, projectMemberTable)
	}}
	addPageFlags(cmd, &flags)
	return cmd
}

func newProjectMemberAddCmd() *cobra.Command {
	var bulk string
	cmd := &cobra.Command{Use: "add <user>", Short: "Add one or more members to the selected project", Args: cobra.MaximumNArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		if bulk != "" && len(args) > 0 {
			return cerr.Usage("--bulk does not take a positional user")
		}
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
		members, err := mgmt.ListMembers(ctx, mc, orgID)
		if err != nil {
			return err
		}
		if bulk != "" {
			refs, err := splitCSV(bulk, "--bulk")
			if err != nil {
				return err
			}
			selected := make([]gen.OrganizationMember, 0, len(refs))
			for _, ref := range refs {
				member, err := resolveMemberRef(members, ref)
				if err != nil {
					return err
				}
				selected = append(selected, *member)
			}
			resp, err := mgmt.AddProjectMembersBulk(ctx, mc, projectID, selected)
			if err != nil {
				return err
			}
			return a.Printer().Object(resp, [][2]string{{"added_user_ids", joinComma(uuidStrings(resp.V.AddedUserIds))}})
		}
		if len(args) != 1 {
			return cobra.ExactArgs(1)(cmd, args)
		}
		member, err := resolveMemberRef(members, args[0])
		if err != nil {
			return err
		}
		resp, err := mgmt.AddProjectMember(ctx, mc, projectID, *member)
		if err != nil {
			return err
		}
		return a.Printer().Object(resp, [][2]string{{"added_user_ids", joinComma(uuidStrings(resp.V.AddedUserIds))}})
	}}
	cmd.Flags().StringVar(&bulk, "bulk", "", "comma-separated user ids/emails/names to add in one request")
	return cmd
}

func newProjectMemberRemoveCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "remove <user>", Short: "Remove one member from the selected project", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		a := FromContext(cmd.Context())
		ctx := cmd.Context()
		if err := a.PreflightConfirm("remove", "project member "+args[0]); err != nil {
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
		members, err := mgmt.ListMembers(ctx, mc, orgID)
		if err != nil {
			return err
		}
		member, err := resolveMemberRef(members, args[0])
		if err != nil {
			return err
		}
		if err := a.Confirm("remove", "project member "+args[0]); err != nil {
			return err
		}
		if err := mgmt.RemoveProjectMember(ctx, mc, projectID, member.UserId.String()); err != nil {
			return err
		}
		return printAck(a, "user_id", member.UserId.String(), "removed", "")
	}}
	return cmd
}
