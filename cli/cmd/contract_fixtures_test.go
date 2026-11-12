// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

func organizationFixture(id, name string, personal bool) map[string]any {
	return map[string]any{
		"id": id, "name": name, "personal": personal, "suspended": false,
		"preview_warning_disabled": false, "budget_stop_grace_seconds": 3600,
		"from_v0": false, "v0_membership_sync_enabled": false, "sso_linked": false, "sso_organization_id": nil,
		"max_sandbox_timeout_seconds": nil,
		"created_at":                  "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
	}
}

func organizationMemberFixture(userID, email, name string) map[string]any {
	return map[string]any{
		"user_id": userID, "email": email, "name": name, "roles": []string{"developer"},
		"joined_at": "2026-09-01T00:00:00Z", "org_permissions": []string{"read:organization"},
		"assignable_project_permissions": []string{"read:sandboxes"},
	}
}

func serviceAccountFixture(id, orgID, name string) map[string]any {
	return map[string]any{
		"id": id, "org_id": orgID, "name": name, "roles": []string{"developer"},
		"org_permissions": []string{"write:sandboxes"}, "live_key_count": 0,
		"created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
	}
}

func archivedServiceAccountFixture(id, orgID, name string) map[string]any {
	account := serviceAccountFixture(id, orgID, name)
	account["archived_at"] = "2026-09-02T00:00:00Z"
	return account
}

func invitationFixture(id, orgID, email string) map[string]any {
	return map[string]any{
		"id": id, "org_id": orgID, "email": email, "roles": []string{"developer"},
		"status": "pending", "created_at": "2026-09-01T00:00:00Z", "expires_at": "2026-10-20T00:00:00Z",
	}
}

func invitationWithAcceptURLFixture(acceptURL string) map[string]any {
	invitation := invitationFixture("11111111-1111-1111-1111-111111111111", testOrgP, "new@example.com")
	invitation["accept_url"] = acceptURL
	return invitation
}
