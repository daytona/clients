// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import "encoding/json"

func fixtureJSON(v any) string {
	raw, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return string(raw)
}

func organizationsFixture() map[string]any {
	return map[string]any{
		"organizations": []map[string]any{
			{"id": testOrgP, "name": "Me", "personal": true, "from_v0": false, "membership": map[string]any{"default_project_id": testProjP1, "roles": []string{"admin"}, "permissions": []string{"read:organization"}, "grantable_project_permissions": []string{"write:sandboxes", "read:sandboxes"}}, "suspended": false, "preview_warning_disabled": false, "v0_membership_sync_enabled": false, "sso_linked": false, "sso_organization_id": nil, "budget_stop_grace_seconds": 3600, "max_sandbox_timeout_seconds": nil, "created_at": "2026-09-13T00:00:00Z", "updated_at": "2026-09-13T00:00:00Z"},
			{"id": testOrgA, "name": "Acme", "personal": false, "from_v0": false, "membership": map[string]any{"default_project_id": testProjA1, "roles": []string{"developer"}, "permissions": []string{}, "grantable_project_permissions": []string{"write:sandboxes"}}, "suspended": false, "preview_warning_disabled": false, "v0_membership_sync_enabled": false, "sso_linked": false, "sso_organization_id": nil, "budget_stop_grace_seconds": 3600, "max_sandbox_timeout_seconds": nil, "created_at": "2026-09-13T00:00:00Z", "updated_at": "2026-09-13T00:00:00Z"},
			{"id": testOrgB, "name": "acme", "personal": false, "from_v0": false, "membership": map[string]any{"default_project_id": testProjP1, "roles": []string{"developer"}, "permissions": []string{}, "grantable_project_permissions": []string{"write:sandboxes"}}, "suspended": false, "preview_warning_disabled": false, "v0_membership_sync_enabled": false, "sso_linked": false, "sso_organization_id": nil, "budget_stop_grace_seconds": 3600, "max_sandbox_timeout_seconds": nil, "created_at": "2026-09-13T00:00:00Z", "updated_at": "2026-09-13T00:00:00Z"},
		},
	}
}

func oidcConfigFixture(issuer string) map[string]any {
	return map[string]any{
		"billing_enabled": false,
		"ssh_gateway":     nil,
		"analytics":       nil,
		// Build identity fields are required by the /config response contract.
		"version":   "2.0.0-alpha.1",
		"build_sha": "0123456789abcdef0123456789abcdef01234567",
		"built_at":  "2026-10-02T14:03:11Z",
		"oidc": map[string]any{
			"provider":      "oidc",
			"issuer":        issuer + "/idp",
			"audience":      "aud",
			"client_id":     "spa",
			"scope":         "openid",
			"cli_client_id": "native",
		},
	}
}

func walletFixture() map[string]any {
	return map[string]any{
		"balance_cents":                     1000,
		"billing_type":                      "pre-paid",
		"currency":                          "usd",
		"free_credits_balance_cents":        0,
		"paid_credits_balance_cents":        1000,
		"total_amount_due_this_month_cents": 0,
		"total_spent_this_month_cents":      0,
		"automatic_top_up":                  map[string]any{"enabled": false, "threshold_cents": nil, "target_cents": nil, "currency": "usd"},
	}
}

// regionCatalogFixture returns a region and its parent regions for GET /regions.
func regionCatalogFixture(id, name, parentRegionID, childRegionID string) []any {
	region := func(id, name string, parents ...string) map[string]any {
		if parents == nil {
			parents = []string{}
		}
		return map[string]any{"id": id, "name": name, "parent_region_ids": parents, "available": true}
	}
	return []any{
		region("region-root", "Root Region"),
		region(parentRegionID, parentRegionID, "region-root"),
		region(childRegionID, childRegionID, "region-root", parentRegionID),
		region(id, name, "region-root", parentRegionID, childRegionID),
	}
}

func usageHistoryFixture(sandboxID string) map[string]any {
	return map[string]any{
		"available":  true,
		"sandbox_id": sandboxID,
		"resolution": "minute",
		"periods": []map[string]any{{
			"start_at":               "2026-09-13T00:00:00Z",
			"duration_ms":            60000,
			"cpu_core_seconds":       1,
			"memory_byte_seconds":    512,
			"memory_peak_bytes":      512,
			"disk_used_bytes":        1024,
			"disk_read_bytes":        0,
			"disk_written_bytes":     0,
			"network_received_bytes": 0,
			"network_sent_bytes":     0,
		}},
	}
}

func currentUsageFixture() map[string]any {
	return map[string]any{
		"amount_cents":       4200,
		"charges":            []any{},
		"currency":           "usd",
		"start_at":           "2026-09-01T00:00:00Z",
		"end_at":             "2026-09-30T00:00:00Z",
		"issued_at":          "2026-09-30T00:00:00Z",
		"status":             "open",
		"taxes_amount_cents": 0,
		"total_amount_cents": 4200,
	}
}

func pastUsageFixture() map[string]any {
	period := currentUsageFixture()
	period["status"] = "closed"
	return map[string]any{"usage_periods": []map[string]any{period}, "next_cursor": nil}
}

func paymentMethodFixture() map[string]any {
	return map[string]any{
		"id":         "pm_123",
		"brand":      "visa",
		"exp_month":  12,
		"exp_year":   2030,
		"is_default": true,
		"last4":      "4242",
	}
}

func invoiceFixture(id string) map[string]any {
	return map[string]any{
		"id":                     id,
		"currency":               "usd",
		"file_url":               "https://invoice.example/file",
		"has_pdf":                true,
		"issued_at":              "2026-09-01T00:00:00Z",
		"number":                 "INV-1",
		"payment_due_at":         "2026-09-15T00:00:00Z",
		"payment_overdue":        false,
		"payment_status":         "succeeded",
		"status":                 "finalized",
		"suspends_at":            nil,
		"subtotal_amount_cents":  500,
		"total_amount_cents":     500,
		"total_due_amount_cents": 0,
		"taxes_amount_cents":     0,
		"type":                   "usage",
	}
}
