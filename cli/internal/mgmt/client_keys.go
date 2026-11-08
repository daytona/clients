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

// ProjectKeysPage reads one page of GET /projects/{project_id}/api-keys.
func ProjectKeysPage(ctx context.Context, c *api.Client, projectID string, cursor string, limit int64) (Page[gen.ProjectApiKey], error) {
	return getPage(ctx, c, "/projects/"+url.PathEscape(projectID)+"/api-keys", cursor, limit, func(resp gen.ProjectApiKeyPage) ([]gen.ProjectApiKey, *string) {
		return resp.ProjectApiKeys, resp.NextCursor
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

// OrgKeysPage reads one page of GET /orgs/{org_id}/api-keys.
func OrgKeysPage(ctx context.Context, c *api.Client, orgID string, cursor string, limit int64) (Page[gen.OrganizationApiKey], error) {
	return getPage(ctx, c, "/orgs/"+url.PathEscape(orgID)+"/api-keys", cursor, limit, func(resp gen.OrganizationApiKeyPage) ([]gen.OrganizationApiKey, *string) {
		return resp.OrganizationApiKeys, resp.NextCursor
	})
}

func RevokeOrgKey(ctx context.Context, c *api.Client, orgID, keyID string) error {
	return c.Do(ctx, http.MethodDelete, "/orgs/"+url.PathEscape(orgID)+"/api-keys/"+url.PathEscape(keyID), nil, nil, nil)
}
