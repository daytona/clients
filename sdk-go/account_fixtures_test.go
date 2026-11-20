// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"testing"
)

const accountTestTimestamp = "2026-11-20T12:34:56.123Z"

func accountKeyFixture() map[string]any {
	return map[string]any{
		"id": "key-id", "name": "automation", "project_id": "project-id",
		"project_permissions": []any{"read:limits", "read:spending"}, "created_at": accountTestTimestamp,
		"created_by": "creator-id", "expires_at": accountTestTimestamp, "last_used_at": accountTestTimestamp,
		"revoked_at": accountTestTimestamp, "key_prefix": "masked", "key_suffix": "display",
		"service_account_id": "service-id", "service_account_name": "automation",
		"context": map[string]any{"org_id": "org-id", "org_name": "Organization", "project_name": "Project"},
	}
}

func accountBudgetFixture() map[string]any {
	return map[string]any{
		"monthly_limit_cents": 12345, "period_start_date": "2026-11-01", "spent_cents": 42,
		"created_at": accountTestTimestamp, "updated_at": accountTestTimestamp, "evaluated_at": accountTestTimestamp,
		"exceeded_at": accountTestTimestamp, "stop_at": accountTestTimestamp,
	}
}

func accountUsageFixture() map[string]any {
	return map[string]any{"limit": 100, "usage": 7}
}

func accountLimitsFixture() map[string]any {
	return map[string]any{
		"sandboxes":    map[string]any{"concurrent": accountUsageFixture(), "total": accountUsageFixture()},
		"image_builds": map[string]any{"concurrent": accountUsageFixture()},
		"storage": map[string]any{
			"volumes": accountUsageFixture(), "images": accountUsageFixture(), "snapshots": accountUsageFixture(),
		},
		"secrets_per_project": accountUsageFixture(), "api_keys_per_project": accountUsageFixture(),
		"api_keys_per_organization": accountUsageFixture(),
		"sandbox_resources":         map[string]any{"max_cores": 2.5, "max_memory_mib": 2048},
		"mac_reservations":          accountUsageFixture(),
		"members":                   map[string]any{"limit": nil, "usage": 3, "pending_invitations": 2},
	}
}

func accountAuditFixture() map[string]any {
	return map[string]any{"targets": []any{
		map[string]any{"target_type": "future_target", "actions": []any{"future_action", "create"}},
	}}
}

type accountTestRead struct {
	name    string
	path    string
	fixture func() map[string]any
	call    func(context.Context, *Client) error
}

func accountTestReads() []accountTestRead {
	return []accountTestRead{
		{"audit", "/audit/actions", accountAuditFixture, func(ctx context.Context, c *Client) error {
			_, err := c.ListAuditActions(ctx)
			return err
		}},
		{"key", "/api-keys/current", accountKeyFixture, func(ctx context.Context, c *Client) error {
			_, err := c.CurrentAPIKey(ctx)
			return err
		}},
		{"budget", "/usage/budget", accountBudgetFixture, func(ctx context.Context, c *Client) error {
			_, err := c.ProjectBudget(ctx)
			return err
		}},
		{"limits", "/orgs/org-id/limits", accountLimitsFixture, func(ctx context.Context, c *Client) error {
			_, err := c.Limits(ctx, &GetLimitsOptions{OrganizationID: "org-id"})
			return err
		}},
	}
}

func accountReadByName(t *testing.T, name string) accountTestRead {
	t.Helper()
	for _, read := range accountTestReads() {
		if read.name == name {
			return read
		}
	}
	t.Fatalf("unknown account read %q", name)
	return accountTestRead{}
}
