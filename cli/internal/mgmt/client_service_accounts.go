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

func ListServiceAccounts(ctx context.Context, c *api.Client, orgID string) ([]gen.OrganizationServiceAccount, error) {
	return collectAll(ctx, func(ctx context.Context, cursor string) ([]gen.OrganizationServiceAccount, *string, error) {
		query := cursorQuery(cursor)
		if query == nil {
			query = url.Values{}
		}
		var resp gen.OrganizationServiceAccountPage
		if err := c.Do(ctx, http.MethodGet, "/orgs/"+url.PathEscape(orgID)+"/service-accounts", query, nil, &resp); err != nil {
			return nil, nil, err
		}
		return resp.ServiceAccounts, resp.NextCursor, nil
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

func ListProjectServiceAccounts(ctx context.Context, c *api.Client, projectID string) ([]gen.ProjectServiceAccount, error) {
	return collectAll(ctx, func(ctx context.Context, cursor string) ([]gen.ProjectServiceAccount, *string, error) {
		var resp gen.ProjectServiceAccountPage
		if err := c.Do(ctx, http.MethodGet, "/projects/"+url.PathEscape(projectID)+"/service-accounts", cursorQuery(cursor), nil, &resp); err != nil {
			return nil, nil, err
		}
		return resp.ServiceAccounts, resp.NextCursor, nil
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
