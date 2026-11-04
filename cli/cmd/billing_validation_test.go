// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"fmt"
	"net/http"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

const billingValidationOrgID = "0000000a-0000-4000-8000-00000000a001"

func TestBillingSettings_missingRequiredCentsFlagReturnsUsage(t *testing.T) {
	tests := []struct {
		name    string
		args    []string
		message string
	}{
		{name: "automatic top-up threshold", args: []string{"billing", "automatic-top-up", "set", "--target-cents", "2000"}, message: "--threshold-cents is required"},
		{name: "automatic top-up target", args: []string{"billing", "automatic-top-up", "set", "--threshold-cents", "1000"}, message: "--target-cents is required"},
		{name: "balance notification threshold", args: []string{"billing", "balance-notification", "set"}, message: "--threshold-cents is required"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Given
			api := newFakeAPI(t)

			// When
			_, stderr, code := runMgmt(t, api, cfgDir(t), append([]string{"--org", billingValidationOrgID}, tt.args...)...)

			// Then
			require.Equal(t, cerr.ExitUsage, code, stderr)
			require.Contains(t, stderr, tt.message)
			require.Empty(t, api.requests)
		})
	}
}

func TestBillingLists_validateExplicitLimitBeforeRequest(t *testing.T) {
	tests := []struct {
		command  string
		path     string
		listKey  string
		maxLimit int
	}{
		{command: "balances", path: "/balances", listKey: "balances", maxLimit: 100},
		{command: "charges", path: "/charges", listKey: "charges", maxLimit: 100},
		{command: "invoices", path: "/invoices", listKey: "invoices", maxLimit: 100},
		{command: "payment-methods", path: "/payment-methods", listKey: "payment_methods", maxLimit: 100},
		{command: "usage-periods", path: "/usage-periods", listKey: "usage_periods", maxLimit: 60},
	}
	for _, tt := range tests {
		for _, limit := range []int{0, -1, tt.maxLimit, tt.maxLimit + 1} {
			t.Run(fmt.Sprintf("%s limit %d", tt.command, limit), func(t *testing.T) {
				// Given
				api := newFakeAPI(t)
				api.on(http.MethodGet, "/orgs/"+billingValidationOrgID+"/billing"+tt.path, http.StatusOK, map[string]any{tt.listKey: []any{}, "next_cursor": nil})

				// When
				_, stderr, code := runMgmt(t, api, cfgDir(t), "--org", billingValidationOrgID, "billing", tt.command, "--limit", fmt.Sprint(limit))

				// Then
				if limit == tt.maxLimit {
					require.Equal(t, 0, code, stderr)
					require.Len(t, api.requests, 1)
					require.Equal(t, fmt.Sprintf("limit=%d", limit), api.requests[0].Query)
					return
				}
				require.Equal(t, cerr.ExitUsage, code, stderr)
				require.Contains(t, stderr, fmt.Sprintf("--limit must be between 1 and %d", tt.maxLimit))
				require.Empty(t, api.requests)
			})
		}
	}
}
