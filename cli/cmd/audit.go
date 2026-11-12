// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"net/url"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/spf13/cobra"
)

func init() { Register(newAuditCmd) }

func newAuditCmd() *cobra.Command {
	var flags pageFlags
	var action, actionNotIn, targetType, targetID, actor, startAt, endAt string
	var status int
	cmd := &cobra.Command{Use: "audit", Short: "Organization audit logs", GroupID: GroupManagement}
	list := &cobra.Command{Use: "list", Short: "List audit log entries", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		for _, bound := range []struct{ flag, value string }{{"start-at", startAt}, {"end-at", endAt}} {
			if cmd.Flags().Changed(bound.flag) {
				if _, err := time.Parse(time.RFC3339, bound.value); err != nil {
					return cerr.Usage("--%s must be an RFC3339 timestamp", bound.flag)
				}
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
		fetch := func(ctx context.Context, cursor string) (output.List[api.Raw[gen.AuditLogEntry]], error) {
			query := url.Values{}
			if flags.limit > 0 {
				query.Set("limit", toString(flags.limit))
			}
			if cursor != "" {
				query.Set("cursor", cursor)
			}
			if action != "" {
				query.Set("action", action)
			}
			if actionNotIn != "" {
				query.Set("action[not_in]", actionNotIn)
			}
			if targetType != "" {
				query.Set("target_type", targetType)
			}
			if targetID != "" {
				query.Set("target_id", targetID)
			}
			if actor != "" {
				query.Set("actor", actor)
			}
			if cmd.Flags().Changed("status") {
				query.Set("status", toString(status))
			}
			if startAt != "" {
				query.Set("start_at", startAt)
			}
			if endAt != "" {
				query.Set("end_at", endAt)
			}
			return getRawList[gen.AuditLogEntry](ctx, mc, "/orgs/"+url.PathEscape(orgID)+"/audit-logs", query, "audit_logs")
		}
		var list output.List[api.Raw[gen.AuditLogEntry]]
		if flags.all {
			list, err = collectPaged(ctx, flags.cursor, fetch)
		} else {
			list, err = fetch(ctx, flags.cursor)
		}
		if err != nil {
			return err
		}
		return listOutRaw(a.Printer(), list, func(rows []gen.AuditLogEntry) output.Table {
			tableRows := make([][]string, 0, len(rows))
			for _, row := range rows {
				tableRows = append(tableRows, []string{row.Action, toString(row.StatusCode), row.TargetType, formatPtr(row.TargetId), formatUUIDPtr(row.ActorKeyId), formatUUIDPtr(row.ActorUserId), row.CreatedAt.Format("2006-01-02T15:04:05Z07:00")})
			}
			return output.Table{Headers: []string{"ACTION", "STATUS", "TARGET_TYPE", "TARGET_ID", "ACTOR_KEY_ID", "ACTOR_USER_ID", "CREATED_AT"}, Rows: tableRows}
		})
	}}
	addPageFlags(list, &flags)
	list.Flags().StringVar(&action, "action", "", "filter by action")
	list.Flags().StringVar(&actionNotIn, "action-not-in", "", "exclude comma-separated actions (not_in)")
	list.Flags().StringVar(&targetType, "target-type", "", "filter by target type")
	list.Flags().StringVar(&targetID, "target-id", "", "filter by target id")
	list.Flags().StringVar(&actor, "actor", "", "filter by actor user id or key id")
	list.Flags().IntVar(&status, "status", 0, "filter by HTTP status")
	list.Flags().StringVar(&startAt, "start-at", "", "inclusive RFC3339 start of the time range")
	list.Flags().StringVar(&endAt, "end-at", "", "exclusive RFC3339 end of the time range")
	cmd.AddCommand(list)
	return cmd
}
