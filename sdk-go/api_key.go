// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"time"
)

// ProjectAPIKeyContext identifies the organization and project of an API key.
type ProjectAPIKeyContext struct {
	// OrgID is the organization identifier.
	OrgID string
	// OrgName is the organization's display name.
	OrgName string
	// ProjectName is the project's display name.
	ProjectName string
}

// ProjectAPIKey describes a project API key without exposing its token.
type ProjectAPIKey struct {
	// ID is the key identifier.
	ID string
	// Name is the key's display name.
	Name string
	// ProjectID identifies the project the key acts in.
	ProjectID string
	// ProjectPermissions lists the granted permission names.
	ProjectPermissions []string
	// CreatedAt is the creation timestamp.
	CreatedAt time.Time
	// CreatedBy identifies the creator, if reported.
	CreatedBy string
	// ExpiresAt is zero when no expiration was reported.
	ExpiresAt time.Time
	// LastUsedAt is zero when no use was reported.
	LastUsedAt time.Time
	// RevokedAt is zero when no revocation was reported.
	RevokedAt time.Time
	// KeyPrefix is the masked display prefix, not a credential.
	KeyPrefix string
	// KeySuffix is the masked display suffix, not a credential.
	KeySuffix string
	// ServiceAccountID identifies the owning service account, if any.
	ServiceAccountID string
	// ServiceAccountName is the owning service account's name, if any.
	ServiceAccountName string
	// Context describes the key's organization and project.
	Context ProjectAPIKeyContext
}

// CurrentAPIKey describes the API key used to authenticate this client.
// The token itself is never returned. Malformed responses return ErrBadRequest.
func (c *Client) CurrentAPIKey(ctx context.Context) (*ProjectAPIKey, error) {
	var raw json.RawMessage
	if err := c.api().do(ctx, apiRequest{Path: "/api-keys/current", Result: &raw}); err != nil {
		return nil, err
	}
	object := accountResponse(raw, "API key")
	keyContext := object.nested("context", false)
	result := &ProjectAPIKey{
		ID: object.text("id", true), Name: object.text("name", true),
		ProjectID: object.text("project_id", true), ProjectPermissions: object.strings("project_permissions"),
		CreatedAt: object.timestamp("created_at", true), CreatedBy: object.text("created_by", false),
		ExpiresAt: object.timestamp("expires_at", false), LastUsedAt: object.timestamp("last_used_at", false),
		RevokedAt: object.timestamp("revoked_at", false), KeyPrefix: object.text("key_prefix", false),
		KeySuffix: object.text("key_suffix", false), ServiceAccountID: object.text("service_account_id", false),
		ServiceAccountName: object.text("service_account_name", false),
		Context: ProjectAPIKeyContext{
			OrgID: keyContext.text("org_id", true), OrgName: keyContext.text("org_name", true),
			ProjectName: keyContext.text("project_name", true),
		},
	}
	if *object.err != nil {
		return nil, *object.err
	}
	return result, nil
}
