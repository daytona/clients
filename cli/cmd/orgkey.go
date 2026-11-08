// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/mgmt"
	"github.com/spf13/cobra"
)

func newOrgKeyCmd() *cobra.Command {
	return newKeyCommand(keyCommandSpec[gen.OrganizationApiKeyWithToken, gen.OrganizationApiKey]{
		use:               "org-key",
		short:             "Organization API keys: mint, list, revoke",
		mintShort:         "Mint an organization API key",
		requireName:       true,
		listShort:         "List organization API keys",
		revokeShort:       "Revoke an organization API key",
		permissionHelp:    "organization permission (repeatable or comma-separated)",
		revokeTargetLabel: "org key",
		listHeaders:       []string{"ID", "NAME", "PERMISSIONS", "EXPIRES_AT", "REVOKED_AT"},
		requireScope: func(a *app.App, ctx context.Context) (string, error) {
			return a.RequireOrg(ctx, pickOrg(a))
		},
		buildMint: func(ctx context.Context, _ *app.App, mc *mgmtClient, orgID, name string, permissions []string, expiresAt *time.Time, _ string) (*api.Raw[gen.OrganizationApiKeyWithToken], error) {
			return mgmt.CreateOrgKey(ctx, mc, orgID, gen.CreateOrganizationApiKeyRequest{Name: name, OrgPermissions: permissions, ExpiresAt: expiresAt})
		},
		list:   mgmt.OrgKeysPage,
		revoke: mgmt.RevokeOrgKey,
		token:  func(resp *gen.OrganizationApiKeyWithToken) string { return resp.Token },
		createdKV: func(resp *gen.OrganizationApiKeyWithToken) [][2]string {
			return [][2]string{{"id", resp.Id.String()}, {"name", resp.Name}, {"org_id", resp.OrgId.String()}, {"key_prefix", formatPtr(resp.KeyPrefix)}, {"org_permissions", joinComma(resp.OrgPermissions)}, {"expires_at", formatTimePtr(resp.ExpiresAt)}}
		},
		listRow: func(row gen.OrganizationApiKey) []string {
			return []string{row.Id.String(), row.Name, joinComma(row.OrgPermissions), formatTimePtr(row.ExpiresAt), formatTimePtr(row.RevokedAt)}
		},
	})
}
