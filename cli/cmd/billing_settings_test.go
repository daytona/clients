// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/json"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestBillingSettings_useContractRoutesAndShapes(t *testing.T) {
	orgID := "0000000a-0000-4000-8000-00000000a001"
	tests := []struct {
		name     string
		args     []string
		method   string
		path     string
		status   int
		response map[string]any
		body     map[string]any
	}{
		{name: "automatic top-up get", args: []string{"billing", "automatic-top-up", "get"}, method: http.MethodGet, path: "/automatic-top-up", status: http.StatusOK, response: automaticTopUpFixture()},
		{name: "automatic top-up set", args: []string{"billing", "automatic-top-up", "set", "--threshold-cents", "1000", "--target-cents", "2000", "--yes"}, method: http.MethodPut, path: "/automatic-top-up", status: http.StatusOK, response: automaticTopUpFixture(), body: map[string]any{"threshold_cents": float64(1000), "target_cents": float64(2000)}},
		{name: "automatic top-up delete", args: []string{"billing", "automatic-top-up", "delete", "--yes"}, method: http.MethodDelete, path: "/automatic-top-up", status: http.StatusNoContent},
		{name: "balance notification get", args: []string{"billing", "balance-notification", "get"}, method: http.MethodGet, path: "/balance-notification", status: http.StatusOK, response: balanceNotificationFixture()},
		{name: "balance notification set", args: []string{"billing", "balance-notification", "set", "--threshold-cents", "500"}, method: http.MethodPut, path: "/balance-notification", status: http.StatusOK, response: balanceNotificationFixture(), body: map[string]any{"threshold_cents": float64(500)}},
		{name: "balance notification delete", args: []string{"billing", "balance-notification", "delete", "--yes"}, method: http.MethodDelete, path: "/balance-notification", status: http.StatusNoContent},
		{name: "coupon redeem", args: []string{"billing", "coupon", "redeem", "SAVE_10"}, method: http.MethodPost, path: "/coupons/redeem", status: http.StatusCreated, response: couponFixture(), body: map[string]any{"code": "SAVE_10"}},
		{name: "plan get", args: []string{"billing", "plan", "get"}, method: http.MethodGet, path: "/plan", status: http.StatusOK, response: billingPlanFixture()},
		{name: "plan list", args: []string{"billing", "plan", "list"}, method: http.MethodGet, path: "/plans", status: http.StatusOK, response: map[string]any{"plans": []map[string]any{planFixture()}}},
		{name: "plan set", args: []string{"billing", "plan", "set", "team", "--yes"}, method: http.MethodPut, path: "/plan", status: http.StatusOK, response: billingPlanFixture(), body: map[string]any{"plan": "team"}},
		{name: "contact get", args: []string{"billing", "contact", "get"}, method: http.MethodGet, path: "/contact", status: http.StatusOK, response: map[string]any{"email": "billing@example.com", "effective_email": "billing@example.com"}},
		{name: "contact set", args: []string{"billing", "contact", "set", "--email", "new@example.com"}, method: http.MethodPut, path: "/contact", status: http.StatusOK, response: map[string]any{"email": "new@example.com", "effective_email": "new@example.com"}, body: map[string]any{"email": "new@example.com"}},
		{name: "contact clear", args: []string{"billing", "contact", "set", "--clear"}, method: http.MethodPut, path: "/contact", status: http.StatusOK, response: map[string]any{"email": nil, "effective_email": "owner@example.com"}, body: map[string]any{"email": nil}},
		{name: "profile", args: []string{"billing", "profile"}, method: http.MethodGet, path: "/profile", status: http.StatusOK, response: billingProfileFixture()},
		{name: "portal session", args: []string{"billing", "portal-session"}, method: http.MethodPost, path: "/portal-sessions", status: http.StatusCreated, response: map[string]any{"url": "https://billing.example/portal", "expires_at": nil}},
		{name: "payment method session", args: []string{"billing", "payment-method-session"}, method: http.MethodPost, path: "/payment-method-sessions", status: http.StatusCreated, response: map[string]any{"url": "https://billing.example/payment-method", "expires_at": "2026-10-20T00:00:00Z"}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			api := newFakeAPI(t)
			path := "/orgs/" + orgID + "/billing" + tt.path
			if tt.status == http.StatusNoContent {
				api.handle(tt.method, path, func(w http.ResponseWriter, _ *http.Request) {
					w.WriteHeader(http.StatusNoContent)
				})
			} else {
				api.on(tt.method, path, tt.status, tt.response)
			}

			stdout, stderr, code := runMgmt(t, api, cfgDir(t), append([]string{"--org", orgID}, tt.args...)...)

			require.Equal(t, 0, code, stderr)
			require.Len(t, api.requests, 1)
			require.Equal(t, tt.body, api.requests[0].Body)
			if tt.status == http.StatusNoContent {
				require.True(t, isAck(t, stdout, "org_id", orgID, "deleted"), stdout)
			}
		})
	}
}

func TestBillingContactSet_rejectsBlankEmail(t *testing.T) {
	api := newFakeAPI(t)

	_, stderr, code := runMgmt(
		t,
		api,
		cfgDir(t),
		"--org",
		"0000000a-0000-4000-8000-00000000a001",
		"billing",
		"contact",
		"set",
		"--email",
		"   ",
	)

	require.Equal(t, 2, code, stderr)
	require.Contains(t, stderr, "--email must not be blank; use --clear to clear the billing contact")
	require.Empty(t, api.requests)
}

func TestBillingUsagePeriods_allFollowsOpaqueCursor(t *testing.T) {
	api := newFakeAPI(t)
	path := "/orgs/0000000a-0000-4000-8000-00000000a001/billing/usage-periods"
	api.handle(http.MethodGet, path, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Query().Get("cursor") == "period_2" {
			writeTestJSON(t, w, usagePeriodPageFixture("closed", nil))
			return
		}
		writeTestJSON(t, w, usagePeriodPageFixture("open", "period_2"))
	})

	stdout, stderr, code := runMgmt(t, api, cfgDir(t), "--org", "0000000a-0000-4000-8000-00000000a001", "-o", "json", "billing", "usage-periods", "--limit", "5", "--all")

	require.Equal(t, 0, code, stderr)
	require.Len(t, mustJSON(t, stdout)["items"].([]any), 2)
	require.Equal(t, "limit=5", api.requests[0].Query)
	require.Equal(t, "cursor=period_2&limit=5", api.requests[1].Query)
}

func automaticTopUpFixture() map[string]any {
	return map[string]any{"enabled": true, "threshold_cents": 1000, "target_cents": 2000, "currency": "usd"}
}

func balanceNotificationFixture() map[string]any {
	return map[string]any{"enabled": true, "threshold_cents": 500, "currency": "usd"}
}

func couponFixture() map[string]any {
	return map[string]any{"code": "SAVE_10", "credit_cents": 1000, "currency": "usd", "credit_expires_at": nil, "redeemed_at": "2026-10-19T00:00:00Z"}
}

func planFixture() map[string]any {
	return map[string]any{"plan": "team", "self_serve": true, "fee_cents": 5000, "monthly_credit_cents": 1000, "currency": "usd", "limits": map[string]any{"max_members": 10, "max_concurrent_sandboxes": 5}, "entitlements": []string{"billing"}}
}

func billingPlanFixture() map[string]any {
	plan := planFixture()
	plan["pending"] = nil
	plan["current_period"] = nil
	return plan
}

func billingProfileFixture() map[string]any {
	return map[string]any{"name": "Acme", "email": "billing@example.com", "phone": "+12025550123", "address": map[string]any{"line1": "1 Main", "line2": "", "city": "San Francisco", "state": "CA", "postal_code": "94105", "country": "US"}, "tax_ids": []any{}}
}

func writeTestJSON(t *testing.T, w http.ResponseWriter, value any) {
	t.Helper()
	require.NoError(t, json.NewEncoder(w).Encode(value))
}

func TestBillingPlanList_nullOrMissingPlansIsAnEmptyList(t *testing.T) {
	t.Setenv("DAYTONA_TEST_SKIP_CONTRACT", "1") // the contract says plans is always a list
	for _, body := range []string{`{"plans":null}`, `{}`, `null`} {
		api := newFakeAPI(t)
		api.handle(http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/billing/plans", func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(body))
		})

		stdout, stderr, code := runMgmt(t, api, cfgDir(t), "--org", "0000000a-0000-4000-8000-00000000a001", "-o", "json", "billing", "plan", "list")

		require.Equal(t, 0, code, stderr)
		require.Equal(t, map[string]any{"items": []any{}, "incomplete": false}, mustJSON(t, stdout), body)
	}
}

// Changing the plan or automatic top-up changes what the organization is
// billed: off a terminal it needs --yes, refused before any request.
func TestBillingSettingsThatChangeBillingNeedConsent(t *testing.T) {
	for _, args := range [][]string{
		{"billing", "plan", "set", "team"},
		{"billing", "automatic-top-up", "set", "--threshold-cents", "1000", "--target-cents", "2000"},
	} {
		api := newFakeAPI(t)
		_, stderr, code := runMgmt(t, api, cfgDir(t), append([]string{"--org", "0000000a-0000-4000-8000-00000000a001"}, args...)...)
		require.Equal(t, 2, code, stderr)
		require.Contains(t, stderr, "--yes")
		require.Empty(t, api.requests, args)
	}
}
