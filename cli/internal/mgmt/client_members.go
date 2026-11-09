// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package mgmt

import (
	"context"
	"net/http"
	"net/url"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	openapi_types "github.com/oapi-codegen/runtime/types"
)

// MembersPage reads one page of GET /orgs/{org_id}/members.
func MembersPage(ctx context.Context, c *api.Client, orgID string, cursor string, limit int64) (Page[gen.OrganizationMember], error) {
	return getPage[gen.OrganizationMember](ctx, c, "/orgs/"+url.PathEscape(orgID)+"/members", "members", cursor, limit)
}

// ListMembers collects every page (name resolvers and pickers; the list commands read one page).
func ListMembers(ctx context.Context, c *api.Client, orgID string) ([]gen.OrganizationMember, error) {
	return collectAll(ctx, func(ctx context.Context, cursor string) ([]gen.OrganizationMember, *string, error) {
		page, err := MembersPage(ctx, c, orgID, cursor, 0)
		return api.Values(page.Items), &page.NextCursor, err
	})
}

func UpdateMember(ctx context.Context, c *api.Client, orgID, userID string, req gen.UpdateMemberRequest) (*api.Raw[gen.OrganizationMember], error) {
	var resp api.Raw[gen.OrganizationMember]
	if err := c.Do(ctx, http.MethodPatch, "/orgs/"+url.PathEscape(orgID)+"/members/"+url.PathEscape(userID), nil, req, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func PreviewMemberUpdate(ctx context.Context, c *api.Client, orgID, userID string, req gen.PreviewMemberUpdateRequest) (*api.Raw[gen.ChangeImpact], error) {
	var resp api.Raw[gen.ChangeImpact]
	path := "/orgs/" + url.PathEscape(orgID) + "/members/" + url.PathEscape(userID) + "/update-preview"
	if err := c.Do(ctx, http.MethodPost, path, nil, req, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func PreviewMemberRemoval(ctx context.Context, c *api.Client, orgID, userID string) (*api.Raw[gen.MemberRemovalPreview], error) {
	var resp api.Raw[gen.MemberRemovalPreview]
	if err := c.Do(ctx, http.MethodGet, "/orgs/"+url.PathEscape(orgID)+"/members/"+url.PathEscape(userID)+"/removal-preview", nil, nil, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func RemoveMember(ctx context.Context, c *api.Client, orgID, userID string) error {
	return c.Do(ctx, http.MethodDelete, "/orgs/"+url.PathEscape(orgID)+"/members/"+url.PathEscape(userID), nil, nil, nil)
}

// ProjectMembersPage reads one page of GET /projects/{project_id}/members.
func ProjectMembersPage(ctx context.Context, c *api.Client, projectID string, cursor string, limit int64) (Page[gen.ProjectMember], error) {
	return getPage[gen.ProjectMember](ctx, c, "/projects/"+url.PathEscape(projectID)+"/members", "members", cursor, limit)
}

func AddProjectMember(ctx context.Context, c *api.Client, projectID string, userID gen.OrganizationMember) (*api.Raw[gen.ProjectMemberList], error) {
	var resp api.Raw[gen.ProjectMemberList]
	if err := c.Do(ctx, http.MethodPost, "/projects/"+url.PathEscape(projectID)+"/members", nil, gen.AddProjectMembersRequest{UserIds: []openapi_types.UUID{userID.UserId}}, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func AddProjectMembersBulk(ctx context.Context, c *api.Client, projectID string, members []gen.OrganizationMember) (*api.Raw[gen.ProjectMemberList], error) {
	ids := make([]openapi_types.UUID, 0, len(members))
	for _, member := range members {
		ids = append(ids, member.UserId)
	}
	var resp api.Raw[gen.ProjectMemberList]
	if err := c.Do(ctx, http.MethodPost, "/projects/"+url.PathEscape(projectID)+"/members", nil, gen.AddProjectMembersRequest{UserIds: ids}, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func RemoveProjectMember(ctx context.Context, c *api.Client, projectID, userID string) error {
	return c.Do(ctx, http.MethodDelete, "/projects/"+url.PathEscape(projectID)+"/members/"+url.PathEscape(userID), nil, nil, nil)
}
