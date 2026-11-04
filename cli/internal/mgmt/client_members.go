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

func ListMembers(ctx context.Context, c *api.Client, orgID string) ([]gen.OrganizationMember, error) {
	return collectAll(ctx, func(ctx context.Context, cursor string) ([]gen.OrganizationMember, *string, error) {
		var resp gen.OrganizationMemberPage
		if err := c.Do(ctx, http.MethodGet, "/orgs/"+url.PathEscape(orgID)+"/members", cursorQuery(cursor), nil, &resp); err != nil {
			return nil, nil, err
		}
		return resp.Members, resp.NextCursor, nil
	})
}

func UpdateMember(ctx context.Context, c *api.Client, orgID, userID string, req gen.UpdateMemberRequest) (*gen.OrganizationMember, error) {
	var resp gen.OrganizationMember
	if err := c.Do(ctx, http.MethodPatch, "/orgs/"+url.PathEscape(orgID)+"/members/"+url.PathEscape(userID), nil, req, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func PreviewMemberUpdate(ctx context.Context, c *api.Client, orgID, userID string, req gen.PreviewMemberUpdateRequest) (*gen.ChangeImpact, error) {
	var resp gen.ChangeImpact
	path := "/orgs/" + url.PathEscape(orgID) + "/members/" + url.PathEscape(userID) + "/update-preview"
	if err := c.Do(ctx, http.MethodPost, path, nil, req, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func PreviewMemberRemoval(ctx context.Context, c *api.Client, orgID, userID string) (*gen.MemberRemovalPreview, error) {
	var resp gen.MemberRemovalPreview
	if err := c.Do(ctx, http.MethodGet, "/orgs/"+url.PathEscape(orgID)+"/members/"+url.PathEscape(userID)+"/removal-preview", nil, nil, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func RemoveMember(ctx context.Context, c *api.Client, orgID, userID string) error {
	return c.Do(ctx, http.MethodDelete, "/orgs/"+url.PathEscape(orgID)+"/members/"+url.PathEscape(userID), nil, nil, nil)
}

func ListProjectMembers(ctx context.Context, c *api.Client, projectID string) ([]gen.ProjectMember, error) {
	return collectAll(ctx, func(ctx context.Context, cursor string) ([]gen.ProjectMember, *string, error) {
		var resp gen.ProjectMemberPage
		if err := c.Do(ctx, http.MethodGet, "/projects/"+url.PathEscape(projectID)+"/members", cursorQuery(cursor), nil, &resp); err != nil {
			return nil, nil, err
		}
		return resp.Members, resp.NextCursor, nil
	})
}

func AddProjectMember(ctx context.Context, c *api.Client, projectID string, userID gen.OrganizationMember) (*gen.ProjectMemberList, error) {
	var resp gen.ProjectMemberList
	if err := c.Do(ctx, http.MethodPost, "/projects/"+url.PathEscape(projectID)+"/members", nil, gen.AddProjectMembersRequest{UserIds: []openapi_types.UUID{userID.UserId}}, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func AddProjectMembersBulk(ctx context.Context, c *api.Client, projectID string, members []gen.OrganizationMember) (*gen.ProjectMemberList, error) {
	ids := make([]openapi_types.UUID, 0, len(members))
	for _, member := range members {
		ids = append(ids, member.UserId)
	}
	var resp gen.ProjectMemberList
	if err := c.Do(ctx, http.MethodPost, "/projects/"+url.PathEscape(projectID)+"/members", nil, gen.AddProjectMembersRequest{UserIds: ids}, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func RemoveProjectMember(ctx context.Context, c *api.Client, projectID, userID string) error {
	return c.Do(ctx, http.MethodDelete, "/projects/"+url.PathEscape(projectID)+"/members/"+url.PathEscape(userID), nil, nil, nil)
}
