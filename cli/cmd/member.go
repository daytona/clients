// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/mgmt"
	"github.com/google/uuid"
	"github.com/spf13/cobra"
)

func init() {
	Register(newMemberCmd)
}

func newMemberCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "member", Short: "Organization members: list, change roles, remove", GroupID: GroupManagement}
	cmd.AddCommand(newMemberListCmd(), newMemberUpdateCmd(), newMemberRemoveCmd())
	return cmd
}

func newMemberListCmd() *cobra.Command {
	var flags pageFlags
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List members of the selected organization",
		Long:  "List members of the selected organization.\nExamples:\n  daytona member list\n  daytona member list --org org-123",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
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
			items, err := mgmtListPage(ctx, flags, func(ctx context.Context, cursor string, limit int64) (mgmt.Page[gen.OrganizationMember], error) {
				return mgmt.MembersPage(ctx, mc, orgID, cursor, limit)
			})
			if err != nil {
				return err
			}
			return listOutRaw(a.Printer(), items, memberTable)
		},
	}
	addPageFlags(cmd, &flags)
	return cmd
}

func newMemberUpdateCmd() *cobra.Command {
	var roleCSV string
	var dryRun bool
	cmd := &cobra.Command{
		Use:   "update <user>",
		Short: "Replace a member's roles",
		Long:  "Replace a member's roles.\nExamples:\n  daytona member update user-123 --role admin\n  daytona member update user@example.com --role developer,auditor --dry-run",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
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
			members, err := mgmt.ListMembers(ctx, mc, orgID)
			if err != nil {
				return err
			}
			member, err := resolveMemberRef(members, args[0])
			if err != nil {
				return err
			}
			req := gen.UpdateMemberRequest{Roles: roles}
			if dryRun {
				preview, err := mgmt.PreviewMemberUpdate(ctx, mc, orgID, member.UserId.String(), gen.PreviewMemberUpdateRequest{Roles: roles})
				if err != nil {
					return err
				}
				return a.Printer().Object(preview, [][2]string{{"user_id", member.UserId.String()}, {"revoked_api_keys", toString(preview.V.RevokedApiKeys)}, {"revoked_preview_grants", toString(preview.V.RevokedPreviewGrants)}})
			}
			updated, err := mgmt.UpdateMember(ctx, mc, orgID, member.UserId.String(), req)
			if err != nil {
				return err
			}
			return a.Printer().Object(updated, [][2]string{{"user_id", member.UserId.String()}, {"roles", joinComma(updated.V.Roles)}})
		},
	}
	addCSVListFlag(cmd.Flags(), &roleCSV, "role", "role slug (repeatable or comma-separated); replaces the member's roles")
	cmd.Flags().BoolVar(&dryRun, "dry-run", false, "preview the revocation count without committing the role change")
	return cmd
}

func newMemberRemoveCmd() *cobra.Command {
	var dryRun bool
	cmd := &cobra.Command{
		Use:   "remove <user>",
		Short: "Remove a member from the selected organization",
		Long:  "Remove a member from the selected organization.\nExamples:\n  daytona member remove user-123 --yes\n  daytona member remove user@example.com --dry-run",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			ctx := cmd.Context()
			if !dryRun {
				if err := a.PreflightConfirm("remove", "member "+args[0]); err != nil {
					return err
				}
			}
			orgID, err := a.RequireOrg(ctx, pickOrg(a))
			if err != nil {
				return err
			}
			mc, err := a.Client(ctx)
			if err != nil {
				return err
			}
			memberID, err := resolveMemberRemovalTarget(ctx, mc, orgID, args[0])
			if err != nil {
				return err
			}
			if dryRun {
				resp, err := mgmt.PreviewMemberRemoval(ctx, mc, orgID, memberID)
				if err != nil {
					return err
				}
				return a.Printer().Object(resp, [][2]string{{"user_id", memberID}, {"revoked_api_keys", toString(resp.V.RevokedApiKeys)}, {"revoked_preview_grants", toString(resp.V.RevokedPreviewGrants)}})
			}
			if err := a.Confirm("remove", "member "+args[0]); err != nil {
				return err
			}
			if err := mgmt.RemoveMember(ctx, mc, orgID, memberID); err != nil {
				return err
			}
			return printAck(a, "user_id", memberID, "removed", "")
		},
	}
	cmd.Flags().BoolVar(&dryRun, "dry-run", false, "show revoked API-key and preview-grant counts without removing the member")
	return cmd
}

func resolveMemberRemovalTarget(ctx context.Context, mc *mgmtClient, orgID, ref string) (string, error) {
	if ref == "me" {
		me, err := mgmt.GetMe(ctx, mc)
		if err != nil {
			return "", err
		}
		return me.V.Id.String(), nil
	}
	if _, err := uuid.Parse(ref); err == nil {
		return ref, nil
	}
	members, err := mgmt.ListMembers(ctx, mc, orgID)
	if err != nil {
		return "", err
	}
	member, err := resolveMemberRef(members, ref)
	if err != nil {
		return "", err
	}
	return member.UserId.String(), nil
}
