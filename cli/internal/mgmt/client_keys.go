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

func CreateProjectKey(ctx context.Context, c *api.Client, projectID string, req gen.CreateProjectApiKeyRequest) (*gen.ProjectApiKeyWithToken, error) {
	var resp gen.ProjectApiKeyWithToken
	if err := c.Do(ctx, http.MethodPost, "/projects/"+url.PathEscape(projectID)+"/api-keys", nil, req, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func ListProjectKeys(ctx context.Context, c *api.Client, projectID string) ([]gen.ProjectApiKey, error) {
	return collectAll(ctx, func(ctx context.Context, cursor string) ([]gen.ProjectApiKey, *string, error) {
		query := cursorQuery(cursor)
		if query == nil {
			query = url.Values{}
		}
		var resp gen.ProjectApiKeyPage
		if err := c.Do(ctx, http.MethodGet, "/projects/"+url.PathEscape(projectID)+"/api-keys", query, nil, &resp); err != nil {
			return nil, nil, err
		}
		return resp.ProjectApiKeys, resp.NextCursor, nil
	})
}

func RevokeProjectKey(ctx context.Context, c *api.Client, projectID, keyID string) error {
	return c.Do(ctx, http.MethodDelete, "/projects/"+url.PathEscape(projectID)+"/api-keys/"+url.PathEscape(keyID), nil, nil, nil)
}

func CreateOrgKey(ctx context.Context, c *api.Client, orgID string, req gen.CreateOrganizationApiKeyRequest) (*gen.OrganizationApiKeyWithToken, error) {
	var resp gen.OrganizationApiKeyWithToken
	if err := c.Do(ctx, http.MethodPost, "/orgs/"+url.PathEscape(orgID)+"/api-keys", nil, req, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func ListOrgKeys(ctx context.Context, c *api.Client, orgID string) ([]gen.OrganizationApiKey, error) {
	return collectAll(ctx, func(ctx context.Context, cursor string) ([]gen.OrganizationApiKey, *string, error) {
		query := cursorQuery(cursor)
		if query == nil {
			query = url.Values{}
		}
		var resp gen.OrganizationApiKeyPage
		if err := c.Do(ctx, http.MethodGet, "/orgs/"+url.PathEscape(orgID)+"/api-keys", query, nil, &resp); err != nil {
			return nil, nil, err
		}
		return resp.OrganizationApiKeys, resp.NextCursor, nil
	})
}

func RevokeOrgKey(ctx context.Context, c *api.Client, orgID, keyID string) error {
	return c.Do(ctx, http.MethodDelete, "/orgs/"+url.PathEscape(orgID)+"/api-keys/"+url.PathEscape(keyID), nil, nil, nil)
}
