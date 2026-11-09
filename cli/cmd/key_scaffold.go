// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/mgmt"
	"github.com/daytona/clients/cli/internal/output"
	openapi_types "github.com/oapi-codegen/runtime/types"
	"github.com/spf13/cobra"
)

type keyCommandSpec[CreatedResp any, ListItem any] struct {
	use               string
	short             string
	mintShort         string
	requireName       bool
	listShort         string
	revokeShort       string
	permissionHelp    string
	revokeTargetLabel string
	listHeaders       []string
	allowServiceAcct  bool
	requireScope      func(*app.App, context.Context) (string, error)
	buildMint         func(context.Context, *app.App, *mgmtClient, string, string, []string, *time.Time, string) (*api.Raw[CreatedResp], error)
	list              func(context.Context, *mgmtClient, string, string, int64) (mgmt.Page[ListItem], error)
	revoke            func(context.Context, *mgmtClient, string, string) error
	token             func(*CreatedResp) string
	createdKV         func(*CreatedResp) [][2]string
	listRow           func(ListItem) []string
}

func newKeyCommand[CreatedResp any, ListItem any](spec keyCommandSpec[CreatedResp, ListItem]) *cobra.Command {
	cmd := &cobra.Command{Use: spec.use, Short: spec.short, GroupID: GroupManagement}
	cmd.AddCommand(newKeyMintCommand(spec), newKeyListCommand(spec), newKeyRevokeCommand(spec))
	return cmd
}

func newKeyMintCommand[CreatedResp any, ListItem any](spec keyCommandSpec[CreatedResp, ListItem]) *cobra.Command {
	var name, permissionCSV, expires, serviceAccount string
	cmd := &cobra.Command{Use: "mint", Short: spec.mintShort, Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		if spec.requireName && strings.TrimSpace(name) == "" {
			return cerr.Usage("--name is required")
		}
		permissions, err := splitCSV(permissionCSV, "--permission")
		if err != nil {
			return err
		}
		expiresAt, err := parseOptionalExpiry(expires)
		if err != nil {
			return err
		}
		a := FromContext(cmd.Context())
		ctx := cmd.Context()
		scopeID, err := spec.requireScope(a, ctx)
		if err != nil {
			return err
		}
		mc, err := a.Client(ctx)
		if err != nil {
			return err
		}
		resp, err := spec.buildMint(ctx, a, mc, scopeID, name, permissions, expiresAt, serviceAccount)
		if err != nil {
			return err
		}
		if a.Format == output.FormatTable {
			if _, err := fmt.Fprintln(a.Stdout, formatTokenOnce(spec.token(&resp.V))); err != nil {
				return err
			}
			return a.Printer().Object(resp, spec.createdKV(&resp.V))
		}
		return a.Printer().Object(resp, nil)
	}}
	cmd.Flags().StringVar(&name, "name", "", "key name")
	addCSVListFlag(cmd.Flags(), &permissionCSV, "permission", spec.permissionHelp)
	cmd.Flags().StringVar(&expires, "expires", "", "expiry as a Go duration (e.g. 720h) or an RFC3339 timestamp")
	flagPlaceholder(cmd.Flags(), "expires", "DURATION|RFC3339")
	if spec.allowServiceAcct {
		cmd.Flags().StringVar(&serviceAccount, "service-account", "", "service account id or name to mint this key for")
	}
	return cmd
}

func newKeyListCommand[CreatedResp any, ListItem any](spec keyCommandSpec[CreatedResp, ListItem]) *cobra.Command {
	var flags pageFlags
	cmd := &cobra.Command{Use: "list", Short: spec.listShort, Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		a := FromContext(cmd.Context())
		ctx := cmd.Context()
		scopeID, err := spec.requireScope(a, ctx)
		if err != nil {
			return err
		}
		mc, err := a.Client(ctx)
		if err != nil {
			return err
		}
		items, err := mgmtListPage(ctx, flags, func(ctx context.Context, cursor string, limit int64) (mgmt.Page[ListItem], error) {
			return spec.list(ctx, mc, scopeID, cursor, limit)
		})
		if err != nil {
			return err
		}
		return listOutRaw(a.Printer(), items, func(rows []ListItem) output.Table {
			tableRows := make([][]string, 0, len(rows))
			for _, row := range rows {
				tableRows = append(tableRows, spec.listRow(row))
			}
			return output.Table{Headers: spec.listHeaders, Rows: tableRows}
		})
	}}
	addPageFlags(cmd, &flags)
	return cmd
}

func newKeyRevokeCommand[CreatedResp any, ListItem any](spec keyCommandSpec[CreatedResp, ListItem]) *cobra.Command {
	return &cobra.Command{Use: "revoke <id>", Short: spec.revokeShort, Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		a := FromContext(cmd.Context())
		if err := a.Confirm("revoke", spec.revokeTargetLabel+" "+args[0]); err != nil {
			return err
		}
		scopeID, err := spec.requireScope(a, cmd.Context())
		if err != nil {
			return err
		}
		mc, err := a.Client(cmd.Context())
		if err != nil {
			return err
		}
		if err := spec.revoke(cmd.Context(), mc, scopeID, args[0]); err != nil {
			return err
		}
		return printAck(a, "id", args[0], "revoked", "")
	}}
}

func resolveKeyServiceAccountID(ctx context.Context, a *app.App, mc *mgmtClient, ref string) (*openapi_types.UUID, error) {
	if strings.TrimSpace(ref) == "" {
		return nil, nil
	}
	orgID, err := a.RequireOrg(ctx, pickOrg(a))
	if err != nil {
		return nil, err
	}
	items, err := mgmt.ListServiceAccounts(ctx, mc, orgID)
	if err != nil {
		return nil, err
	}
	acct, err := resolveServiceAccountRef(items, ref)
	if err != nil {
		return nil, err
	}
	return &acct.Id, nil
}
