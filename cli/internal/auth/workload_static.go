// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/mgmt"
)

// exchangeStaticForProjectKey makes the single exchange allowed after a
// workload rejection, using only the same static bearer and API origin.
func exchangeStaticForProjectKey(ctx context.Context, a *app.App, base, bearer, projectID, rejected string) (string, error) {
	mc := api.New(base, bearer, app.Version)
	orgs, err := mgmt.ListOrgs(ctx, mc)
	if err != nil {
		return "", err
	}
	var org *gen.OrganizationListItem
	if a.Profile.OrgID != "" {
		org, err = mgmt.ResolveOrg(orgs, a.Profile.OrgID)
		if err != nil {
			return "", err
		}
	} else {
		var lookupErr error
		for i := range orgs {
			ps, err := mgmt.ListProjects(ctx, mc, orgs[i].Id.String())
			if err != nil {
				lookupErr = err
				continue
			}
			for _, p := range ps {
				if p.Id.String() == projectID {
					org = &orgs[i]
					break
				}
			}
			if org != nil {
				break
			}
		}
		if org == nil {
			if lookupErr != nil {
				return "", lookupErr
			}
			return "", cerr.New(cerr.ExitNotFound, "project %s is not in any organization this credential can see", projectID).
				WithHint("pass --org to name the organization that owns it")
		}
	}
	perms := org.Membership.GrantableProjectPermissions
	if len(perms) == 0 {
		return "", cerr.New(cerr.ExitForbidden, "this credential grants no project permissions in %s to mint a key with", org.Name)
	}
	return (keyMint{app: a, client: mc, project: projectID, source: staticSource(base, bearer), rejected: rejected, permissions: perms}).mint(ctx)
}
