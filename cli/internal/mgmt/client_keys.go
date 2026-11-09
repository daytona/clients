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

func CreateProjectKey(ctx context.Context, c *api.Client, projectID string, req gen.CreateProjectApiKeyRequest) (*api.Raw[gen.ProjectApiKeyWithToken], error) {
	var resp api.Raw[gen.ProjectApiKeyWithToken]
	if err := c.Do(ctx, http.MethodPost, "/projects/"+url.PathEscape(projectID)+"/api-keys", nil, req, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// ProjectKeysPage reads one page of GET /projects/{project_id}/api-keys.
func ProjectKeysPage(ctx context.Context, c *api.Client, projectID string, cursor string, limit int64) (Page[gen.ProjectApiKey], error) {
	return getPage[gen.ProjectApiKey](ctx, c, "/projects/"+url.PathEscape(projectID)+"/api-keys", "project_api_keys", cursor, limit)
}

func RevokeProjectKey(ctx context.Context, c *api.Client, projectID, keyID string) error {
	return c.Do(ctx, http.MethodDelete, "/projects/"+url.PathEscape(projectID)+"/api-keys/"+url.PathEscape(keyID), nil, nil, nil)
}

func CreateOrgKey(ctx context.Context, c *api.Client, orgID string, req gen.CreateOrganizationApiKeyRequest) (*api.Raw[gen.OrganizationApiKeyWithToken], error) {
	var resp api.Raw[gen.OrganizationApiKeyWithToken]
	if err := c.Do(ctx, http.MethodPost, "/orgs/"+url.PathEscape(orgID)+"/api-keys", nil, req, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// OrgKeysPage reads one page of GET /orgs/{org_id}/api-keys.
func OrgKeysPage(ctx context.Context, c *api.Client, orgID string, cursor string, limit int64) (Page[gen.OrganizationApiKey], error) {
	return getPage[gen.OrganizationApiKey](ctx, c, "/orgs/"+url.PathEscape(orgID)+"/api-keys", "organization_api_keys", cursor, limit)
}

func RevokeOrgKey(ctx context.Context, c *api.Client, orgID, keyID string) error {
	return c.Do(ctx, http.MethodDelete, "/orgs/"+url.PathEscape(orgID)+"/api-keys/"+url.PathEscape(keyID), nil, nil, nil)
}
