// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"fmt"
	"net/url"
	"regexp"
	"strings"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/mgmt"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/spf13/cobra"
)

func init() {
	Register(newInviteCmd)
}

func newInviteCmd() *cobra.Command {
	// Run the parent to show help for bare `daytona invite` and return a
	// usage error for an unknown subcommand before making any request.
	cmd := &cobra.Command{Use: "invite", Short: "Organization invitations: create, list, revoke, get, accept, decline", GroupID: GroupManagement, Args: rejectUnknownSubcommand, RunE: func(cmd *cobra.Command, _ []string) error {
		return cmd.Help()
	}}
	cmd.AddCommand(newInviteCreateCmd(), newInviteListCmd(), newInviteRevokeCmd(), newInviteGetCmd(), newInviteAcceptCmd(), newInviteDeclineCmd())
	return cmd
}

func inviteTable(items []gen.Invitation) output.Table {
	rows := make([][]string, 0, len(items))
	for _, item := range items {
		rows = append(rows, []string{item.Id.String(), item.Email, "", joinComma(item.Roles), inviteStatus(item)})
	}
	return output.Table{Headers: []string{"ID", "EMAIL", "ORG", "ROLES", "STATUS"}, Rows: rows}
}

// A refusal outranks expiry: declining is only possible before the expiry, so
// declined_at records that the invitation was declined. An absent expires_at
// without an explicit status reads as pending.
func inviteStatus(item gen.Invitation) string {
	if item.DeclinedAt != nil {
		return "declined"
	}
	if !item.ExpiresAt.IsZero() && item.ExpiresAt.Before(time.Now()) {
		return "expired"
	}
	return "pending"
}

// invitationTokenRe is the wire shape of an invitation token: `dtni_` plus
// exactly 43 base64url characters (the contract's `token` field). Anything
// else is refused before a request is made, so a mangled paste is a usage
// error and not a 404 that reads as "ask for a re-invite".
var invitationTokenRe = regexp.MustCompile(`^dtni_[A-Za-z0-9_-]{43}$`)

// invitationTokenArg turns the positional argument of get/accept/decline
// into the `dtni_` token the invitee routes take: a link carrying
// `?token=…` — the absolute one from the invitation email, the relative
// `/dashboard/user/invitations?token=…` that `invite create` prints when the
// deployment sends no email, or a scheme-less copy of either — yields its
// `token` query value; a bare token is used as is. Mail clients and chat
// wrap links in `<…>` or quotes, so those are stripped first. An empty
// argument, a link without a token, or anything not shaped like a token is
// a usage error.
func invitationTokenArg(arg string) (string, error) {
	token := unwrapPasted(arg)
	// A link: the token is the `token` query value. The query is taken
	// textually (after the first `?`, before any `#`) rather than through
	// url.Parse, so a scheme-less host, a bad port, or a hash-router path
	// still yields the token instead of falling through as one.
	if i := strings.IndexByte(token, '?'); i >= 0 {
		query := token[i+1:]
		if j := strings.IndexByte(query, '#'); j >= 0 {
			query = query[:j]
		}
		values, _ := url.ParseQuery(query)
		token = strings.TrimSpace(values.Get("token"))
	}
	if !invitationTokenRe.MatchString(token) {
		return "", cerr.Usage("invitation token or link required")
	}
	return token, nil
}

// unwrapPasted trims whitespace and the `<…>`, `"…"` and `'…'` wrappers a
// pasted link or token tends to arrive in, repeating until nothing is left
// to strip.
func unwrapPasted(s string) string {
	for {
		s = strings.TrimSpace(s)
		if len(s) < 2 {
			return s
		}
		first, last := s[0], s[len(s)-1]
		if (first == '<' && last == '>') || (first == '"' && last == '"') || (first == '\'' && last == '\'') {
			s = s[1 : len(s)-1]
			continue
		}
		return s
	}
}

func newInviteCreateCmd() *cobra.Command {
	var roleCSV string
	cmd := &cobra.Command{Use: "create <email>", Short: "Invite someone to the selected organization", Long: "Invite someone to the selected organization by email. The invitee receives a link to accept the invitation; when the deployment sends no email, the link is printed instead.", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		// Omitted --role: the server applies its documented default
		// (`developer`). An explicit empty value is still a usage error.
		var roles []string
		if cmd.Flags().Changed("role") {
			var err error
			if roles, err = splitCSV(roleCSV, "--role"); err != nil {
				return err
			}
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
		resp, err := mgmt.CreateInvitation(ctx, mc, orgID, gen.CreateInvitationRequest{Email: args[0], Roles: rolesOrNil(roles)})
		if err != nil {
			return err
		}
		// When the response includes accept_url, print the invitation link
		// on its own line. JSON/YAML keep the complete response.
		if resp.V.AcceptUrl != nil && a.Format == output.FormatTable {
			// A relative accept_url cannot be opened directly: print its token
			// for `daytona invite accept <token>`. If it cannot be parsed,
			// preserve the link rather than dropping the invitation.
			token, tokenErr := "", error(nil)
			if strings.HasPrefix(*resp.V.AcceptUrl, "/") {
				token, tokenErr = invitationTokenArg(*resp.V.AcceptUrl)
			}
			if token != "" && tokenErr == nil {
				if _, err := fmt.Fprintf(a.Stdout, "Invitation token: %s\nNo invitation email is sent by this deployment; give the invitee this token for daytona invite accept <token>.\n", output.SanitizeLine(token)); err != nil {
					return err
				}
			} else if _, err := fmt.Fprintf(a.Stdout, "Invitation link: %s\nNo invitation email is sent by this deployment; share the link with the invitee.\n", output.SanitizeLine(*resp.V.AcceptUrl)); err != nil {
				return err
			}
		}
		return a.Printer().Object(resp, [][2]string{{"id", resp.V.Id.String()}, {"email", resp.V.Email}, {"roles", joinComma(resp.V.Roles)}, {"expires_at", resp.V.ExpiresAt.Format(time.RFC3339)}})
	}}
	addCSVListFlag(cmd.Flags(), &roleCSV, "role", "role slug (repeatable or comma-separated)")
	return cmd
}

func newInviteListCmd() *cobra.Command {
	var flags pageFlags
	cmd := &cobra.Command{Use: "list", Short: "List invitations for the selected organization", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
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
		items, err := mgmtListPage(ctx, flags, func(ctx context.Context, cursor string, limit int64) (mgmt.Page[gen.Invitation], error) {
			return mgmt.OrgInvitationsPage(ctx, mc, orgID, cursor, limit)
		})
		if err != nil {
			return err
		}
		return listOutRaw(a.Printer(), items, inviteTable)
	}}
	addPageFlags(cmd, &flags)
	return cmd
}

func newInviteRevokeCmd() *cobra.Command {
	return &cobra.Command{Use: "revoke <id>", Short: "Revoke one pending or declined invitation", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		a := FromContext(cmd.Context())
		ctx := cmd.Context()
		if err := a.PreflightConfirm("revoke", "invitation "+args[0]); err != nil {
			return err
		}
		orgID, err := a.RequireOrg(ctx, pickOrg(a))
		if err != nil {
			return err
		}
		if err := a.Confirm("revoke", "invitation "+args[0]); err != nil {
			return err
		}
		mc, err := a.Client(ctx)
		if err != nil {
			return err
		}
		if err := mgmt.RevokeInvitation(ctx, mc, orgID, args[0]); err != nil {
			return err
		}
		return printAck(a, "id", args[0], "revoked", "")
	}}
}

func newInviteGetCmd() *cobra.Command {
	return &cobra.Command{Use: "get <token|link>", Short: "Show the invitation behind a token or invitation link", Long: "Show who invited you to which organization, with which roles, and until when — before accepting or declining. Takes the dtni_ token or the full link from the invitation email.", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		token, err := invitationTokenArg(args[0])
		if err != nil {
			return err
		}
		a := FromContext(cmd.Context())
		mc, err := a.Client(cmd.Context())
		if err != nil {
			return err
		}
		resp, err := mgmt.LookupInvitation(cmd.Context(), mc, token)
		if err != nil {
			return err
		}
		invitedBy := ""
		if resp.V.InvitedBy != nil {
			invitedBy = *resp.V.InvitedBy
		}
		return a.Printer().Object(resp, [][2]string{{"org", resp.V.OrgName}, {"org_id", resp.V.OrgId.String()}, {"roles", joinComma(resp.V.Roles)}, {"invited_by", invitedBy}, {"email", resp.V.Email}, {"expires_at", resp.V.ExpiresAt.Format(time.RFC3339)}})
	}}
}

func newInviteAcceptCmd() *cobra.Command {
	return &cobra.Command{Use: "accept <token|link>", Short: "Accept an invitation by its token or invitation link", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		token, err := invitationTokenArg(args[0])
		if err != nil {
			return err
		}
		a := FromContext(cmd.Context())
		mc, err := a.Client(cmd.Context())
		if err != nil {
			return err
		}
		resp, err := mgmt.AcceptInvitation(cmd.Context(), mc, token)
		if err != nil {
			return err
		}
		return a.Printer().Object(resp, [][2]string{{"user_id", resp.V.UserId.String()}, {"roles", joinComma(resp.V.Roles)}})
	}}
}

func newInviteDeclineCmd() *cobra.Command {
	return &cobra.Command{Use: "decline <token|link>", Short: "Decline an invitation by its token or invitation link", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		token, err := invitationTokenArg(args[0])
		if err != nil {
			return err
		}
		a := FromContext(cmd.Context())
		mc, err := a.Client(cmd.Context())
		if err != nil {
			return err
		}
		if err := mgmt.DeclineInvitation(cmd.Context(), mc, token); err != nil {
			return err
		}
		return a.Printer().Object(map[string]any{"declined": true}, [][2]string{{"declined", "true"}})
	}}
}

// rolesOrNil leaves `roles` out of the body when --role was not given, so the
// server's default applies.
func rolesOrNil(roles []string) *[]string {
	if roles == nil {
		return nil
	}
	return &roles
}
