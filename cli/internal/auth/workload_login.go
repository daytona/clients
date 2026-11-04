// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/mgmt"
)

// WorkloadCredential is the key path for login-sourced workload/toolbox calls.
// Explicit/static credentials retain their existing as-is behavior.
func WorkloadCredential(ctx context.Context, a *app.App, projectID, rejected string) (string, error) {
	if a.CredentialSource != "profile-oidc" {
		return a.Credential(ctx)
	}
	mc, err := a.Client(ctx)
	if err != nil {
		return "", err
	}
	orgs, err := mgmt.ListOrgs(ctx, mc)
	if err != nil {
		return "", err
	}
	org, err := mgmt.ResolveOrg(orgs, a.Profile.OrgID)
	if err != nil {
		return "", err
	}
	perms := org.Membership.GrantableProjectPermissions
	if len(perms) == 0 {
		return "", cerr.New(cerr.ExitForbidden, "your roles in %s grant no project permissions, so no workload key can be minted", org.Name).
			WithHint("ask an organization admin for a role with sandbox permissions, or use an API key")
	}
	return (keyMint{app: a, client: mc, project: projectID, rejected: rejected, permissions: perms}).mint(ctx)
}
