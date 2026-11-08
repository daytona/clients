// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package mgmt

import (
	"context"
	"net/http"
	"net/url"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
)

func CreateServiceAccount(ctx context.Context, c *api.Client, orgID string, req gen.CreateServiceAccountRequest) (*gen.OrganizationServiceAccount, error) {
	var resp gen.OrganizationServiceAccount
	if err := c.Do(ctx, http.MethodPost, "/orgs/"+url.PathEscape(orgID)+"/service-accounts", nil, req, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// ServiceAccountsPage reads one page of GET /orgs/{org_id}/service-accounts.
func ServiceAccountsPage(ctx context.Context, c *api.Client, orgID string, cursor string, limit int64) (Page[gen.OrganizationServiceAccount], error) {
	return getPage(ctx, c, "/orgs/"+url.PathEscape(orgID)+"/service-accounts", cursor, limit, func(resp gen.OrganizationServiceAccountPage) ([]gen.OrganizationServiceAccount, *string) {
		return resp.ServiceAccounts, resp.NextCursor
	})
}

// ListServiceAccounts collects every page (name resolvers and pickers; the list commands read one page).
func ListServiceAccounts(ctx context.Context, c *api.Client, orgID string) ([]gen.OrganizationServiceAccount, error) {
	return collectAll(ctx, func(ctx context.Context, cursor string) ([]gen.OrganizationServiceAccount, *string, error) {
		page, err := ServiceAccountsPage(ctx, c, orgID, cursor, 0)
		return page.Items, &page.NextCursor, err
	})
}

func GetServiceAccount(ctx context.Context, c *api.Client, orgID, saID string) (*gen.OrganizationServiceAccount, error) {
	var resp gen.OrganizationServiceAccount
	if err := c.Do(ctx, http.MethodGet, "/orgs/"+url.PathEscape(orgID)+"/service-accounts/"+url.PathEscape(saID), nil, nil, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func UpdateServiceAccount(ctx context.Context, c *api.Client, orgID, saID string, req gen.UpdateServiceAccountRequest) (*gen.OrganizationServiceAccount, error) {
	var resp gen.OrganizationServiceAccount
	if err := c.Do(ctx, http.MethodPatch, "/orgs/"+url.PathEscape(orgID)+"/service-accounts/"+url.PathEscape(saID), nil, req, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func DeleteServiceAccount(ctx context.Context, c *api.Client, orgID, saID string) error {
	return c.Do(ctx, http.MethodDelete, "/orgs/"+url.PathEscape(orgID)+"/service-accounts/"+url.PathEscape(saID), nil, nil, nil)
}

// ProjectServiceAccountsPage reads one page of GET /projects/{project_id}/service-accounts.
func ProjectServiceAccountsPage(ctx context.Context, c *api.Client, projectID string, cursor string, limit int64) (Page[gen.ProjectServiceAccount], error) {
	return getPage(ctx, c, "/projects/"+url.PathEscape(projectID)+"/service-accounts", cursor, limit, func(resp gen.ProjectServiceAccountPage) ([]gen.ProjectServiceAccount, *string) {
		return resp.ServiceAccounts, resp.NextCursor
	})
}

func AddProjectServiceAccount(ctx context.Context, c *api.Client, projectID string, saID gen.OrganizationServiceAccount) (*gen.ProjectServiceAccountList, error) {
	var resp gen.ProjectServiceAccountList
	if err := c.Do(ctx, http.MethodPost, "/projects/"+url.PathEscape(projectID)+"/service-accounts", nil, gen.AddProjectServiceAccountRequest{ServiceAccountId: saID.Id}, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func RemoveProjectServiceAccount(ctx context.Context, c *api.Client, projectID, saID string) error {
	return c.Do(ctx, http.MethodDelete, "/projects/"+url.PathEscape(projectID)+"/service-accounts/"+url.PathEscape(saID), nil, nil, nil)
}
