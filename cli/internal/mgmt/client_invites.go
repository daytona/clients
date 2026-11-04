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

func CreateInvitation(ctx context.Context, c *api.Client, orgID string, req gen.CreateInvitationRequest) (*gen.InvitationWithToken, error) {
	var resp gen.InvitationWithToken
	if err := c.Do(ctx, http.MethodPost, "/orgs/"+url.PathEscape(orgID)+"/invitations", nil, req, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func ListOrgInvitations(ctx context.Context, c *api.Client, orgID string) ([]gen.Invitation, error) {
	return collectAll(ctx, func(ctx context.Context, cursor string) ([]gen.Invitation, *string, error) {
		var resp gen.InvitationPage
		if err := c.Do(ctx, http.MethodGet, "/orgs/"+url.PathEscape(orgID)+"/invitations", cursorQuery(cursor), nil, &resp); err != nil {
			return nil, nil, err
		}
		return resp.Invitations, resp.NextCursor, nil
	})
}

func RevokeInvitation(ctx context.Context, c *api.Client, orgID, invitationID string) error {
	return c.Do(ctx, http.MethodDelete, "/orgs/"+url.PathEscape(orgID)+"/invitations/"+url.PathEscape(invitationID), nil, nil, nil)
}

// The three invitee routes carry the single-use `dtni_` token in the request
// body, never the path or query.

func LookupInvitation(ctx context.Context, c *api.Client, token string) (*gen.InvitationLookup, error) {
	var resp gen.InvitationLookup
	if err := c.Do(ctx, http.MethodPost, "/invitations/lookup", nil, gen.LookupInvitationRequest{Token: token}, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func AcceptInvitation(ctx context.Context, c *api.Client, token string) (*gen.OrganizationMember, error) {
	var resp gen.OrganizationMember
	if err := c.Do(ctx, http.MethodPost, "/invitations/accept", nil, gen.AcceptInvitationRequest{Token: token}, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

func DeclineInvitation(ctx context.Context, c *api.Client, token string) error {
	return c.Do(ctx, http.MethodPost, "/invitations/decline", nil, gen.DeclineInvitationRequest{Token: token}, nil)
}
