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

func CreateServiceAccount(ctx context.Context, c *api.Client, orgID string, req gen.CreateServiceAccountRequest) (*api.Raw[gen.OrganizationServiceAccount], error) {
	var resp api.Raw[gen.OrganizationServiceAccount]
	if err := c.Do(ctx, http.MethodPost, "/orgs/"+url.PathEscape(orgID)+"/service-accounts", nil, req, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// ServiceAccountsPage reads one page of GET /orgs/{org_id}/service-accounts.
func ServiceAccountsPage(ctx context.Context, c *api.Client, orgID string, cursor string, limit int64) (Page[gen.OrganizationServiceAccount], error) {
	return getPage[gen.OrganizationServiceAccount](ctx, c, "/orgs/"+url.PathEscape(orgID)+"/service-accounts", "service_accounts", cursor, limit)
}

// ListServiceAccounts collects every page (name resolvers and pickers; the list commands read one page).
func ListServiceAccounts(ctx context.Context, c *api.Client, orgID string) ([]gen.OrganizationServiceAccount, error) {
	return collectAll(ctx, func(ctx context.Context, cursor string) ([]gen.OrganizationServiceAccount, *string, error) {
		page, err := ServiceAccountsPage(ctx, c, orgID, cursor, 0)
		return api.Values(page.Items), &page.NextCursor, err
	})
}

func GetServiceAccount(ctx context.Context, c *api.Client, orgID, saID string) (*api.Raw[gen.OrganizationServiceAccount], error) {
	var resp api.Raw[gen.OrganizationServiceAccount]
	if err := c.Do(ctx, http.MethodGet, "/orgs/"+url.PathEscape(orgID)+"/service-accounts/"+url.PathEscape(saID), nil, nil, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func UpdateServiceAccount(ctx context.Context, c *api.Client, orgID, saID string, req gen.UpdateServiceAccountRequest) (*api.Raw[gen.OrganizationServiceAccount], error) {
	var resp api.Raw[gen.OrganizationServiceAccount]
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
	return getPage[gen.ProjectServiceAccount](ctx, c, "/projects/"+url.PathEscape(projectID)+"/service-accounts", "service_accounts", cursor, limit)
}

func AddProjectServiceAccount(ctx context.Context, c *api.Client, projectID string, saID gen.OrganizationServiceAccount) (*api.Raw[gen.ProjectServiceAccountList], error) {
	var resp api.Raw[gen.ProjectServiceAccountList]
	if err := c.Do(ctx, http.MethodPost, "/projects/"+url.PathEscape(projectID)+"/service-accounts", nil, gen.AddProjectServiceAccountRequest{ServiceAccountId: saID.Id}, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func RemoveProjectServiceAccount(ctx context.Context, c *api.Client, projectID, saID string) error {
	return c.Do(ctx, http.MethodDelete, "/projects/"+url.PathEscape(projectID)+"/service-accounts/"+url.PathEscape(saID), nil, nil, nil)
}
