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

func CreateInvitation(ctx context.Context, c *api.Client, orgID string, req gen.CreateInvitationRequest) (*api.Raw[gen.InvitationWithToken], error) {
	var resp api.Raw[gen.InvitationWithToken]
	if err := c.Do(ctx, http.MethodPost, "/orgs/"+url.PathEscape(orgID)+"/invitations", nil, req, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// OrgInvitationsPage reads one page of GET /orgs/{org_id}/invitations.
func OrgInvitationsPage(ctx context.Context, c *api.Client, orgID string, cursor string, limit int64) (Page[gen.Invitation], error) {
	return getPage[gen.Invitation](ctx, c, "/orgs/"+url.PathEscape(orgID)+"/invitations", "invitations", cursor, limit)
}

func RevokeInvitation(ctx context.Context, c *api.Client, orgID, invitationID string) error {
	return c.Do(ctx, http.MethodDelete, "/orgs/"+url.PathEscape(orgID)+"/invitations/"+url.PathEscape(invitationID), nil, nil, nil)
}

// The three invitee routes carry the single-use `dtni_` token in the request
// body, never the path or query.

func LookupInvitation(ctx context.Context, c *api.Client, token string) (*api.Raw[gen.InvitationLookup], error) {
	var resp api.Raw[gen.InvitationLookup]
	if err := c.Do(ctx, http.MethodPost, "/invitations/lookup", nil, gen.LookupInvitationRequest{Token: token}, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func AcceptInvitation(ctx context.Context, c *api.Client, token string) (*api.Raw[gen.OrganizationMember], error) {
	var resp api.Raw[gen.OrganizationMember]
	if err := c.Do(ctx, http.MethodPost, "/invitations/accept", nil, gen.AcceptInvitationRequest{Token: token}, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func DeclineInvitation(ctx context.Context, c *api.Client, token string) error {
	return c.Do(ctx, http.MethodPost, "/invitations/decline", nil, gen.DeclineInvitationRequest{Token: token}, nil)
}
