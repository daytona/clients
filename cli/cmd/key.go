// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"strings"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/mgmt"
	"github.com/spf13/cobra"
)

func init() {
	Register(newKeyCmd)
	Register(newOrgKeyCmd)
}

func newKeyCmd() *cobra.Command {
	return newKeyCommand(keyCommandSpec[gen.ProjectApiKeyWithToken, gen.ProjectApiKey]{
		use:               "key",
		short:             "Project API keys: mint, list, revoke",
		mintShort:         "Mint a project API key",
		listShort:         "List project API keys",
		revokeShort:       "Revoke a project API key",
		permissionHelp:    "project permission (repeatable or comma-separated)",
		revokeTargetLabel: "key",
		listHeaders:       []string{"ID", "NAME", "PERMISSIONS", "EXPIRES_AT", "REVOKED_AT"},
		allowServiceAcct:  true,
		requireScope: func(a *app.App, ctx context.Context) (string, error) {
			return a.RequireProject(ctx, pickProject(a))
		},
		buildMint: func(ctx context.Context, a *app.App, mc *mgmtClient, projectID, name string, permissions []string, expiresAt *time.Time, serviceAccount string) (*api.Raw[gen.ProjectApiKeyWithToken], error) {
			serviceAccountID, err := resolveKeyServiceAccountID(ctx, a, mc, serviceAccount)
			if err != nil {
				return nil, err
			}
			req := gen.CreateProjectApiKeyRequest{ProjectPermissions: permissions, ExpiresAt: expiresAt, ServiceAccountId: serviceAccountID}
			if strings.TrimSpace(name) != "" {
				req.Name = mgmt.Ref(name)
			}
			return mgmt.CreateProjectKey(ctx, mc, projectID, req)
		},
		list:   mgmt.ProjectKeysPage,
		revoke: mgmt.RevokeProjectKey,
		token:  func(resp *gen.ProjectApiKeyWithToken) string { return resp.Token },
		createdKV: func(resp *gen.ProjectApiKeyWithToken) [][2]string {
			return [][2]string{{"id", resp.Id.String()}, {"name", resp.Name}, {"project_id", resp.ProjectId.String()}, {"key_prefix", formatPtr(resp.KeyPrefix)}, {"project_permissions", joinComma(resp.ProjectPermissions)}, {"expires_at", formatTimePtr(resp.ExpiresAt)}}
		},
		listRow: func(row gen.ProjectApiKey) []string {
			return []string{row.Id.String(), row.Name, joinComma(row.ProjectPermissions), formatTimePtr(row.ExpiresAt), formatTimePtr(row.RevokedAt)}
		},
	})
}
