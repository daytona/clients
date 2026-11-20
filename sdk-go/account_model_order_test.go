// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestReservationMalformedFieldsHaveStableOrder(t *testing.T) {
	for _, fields := range [][]string{
		{"id", "org_id", "project_id", "region_id", "status", "origin"},
		{"model", "renewal_error", "end_reason"},
		{"slots_used", "period_n"},
		{"auto_renew", "renewal_pending"},
		{"created_at", "activated_at", "pending_mac_started_at", "period_started_at", "period_end", "reissued_at", "ended_at"},
	} {
		t.Run(fields[0], func(t *testing.T) {
			// Given several malformed fields in the same response.
			fixture := reservationFixture()
			for _, name := range fields {
				fixture[name] = []string{}
			}
			data, err := json.Marshal(fixture)
			require.NoError(t, err)
			// When parsing repeatedly, map iteration must not choose the failure.
			for range 100 {
				_, parseErr := parseReservation(data)
				// Then the first field in declaration order always wins.
				require.ErrorContains(t, parseErr, "response field "+fields[0]+" ")
			}
		})
	}
}

func TestMacModelMalformedFieldsHaveStableOrder(t *testing.T) {
	for _, fields := range [][]string{
		{"id", "name"},
		{"cores", "memory_mib", "disk_mib", "sandbox_disk_mib", "max_sandboxes"},
	} {
		t.Run(fields[0], func(t *testing.T) {
			// Given several malformed model fields.
			fixture := macModelFixture()
			for _, name := range fields {
				fixture[name] = []string{}
			}
			data, err := json.Marshal(fixture)
			require.NoError(t, err)
			// When parsing repeatedly.
			for range 100 {
				_, parseErr := parseMacModel(data)
				// Then errors consistently identify the first field.
				require.ErrorContains(t, parseErr, "response field "+fields[0]+" ")
			}
		})
	}
}

func TestMacResourcesMalformedFieldsHaveStableOrder(t *testing.T) {
	// Given all nested resource fields malformed.
	fixture := macModelFixture()
	fixture["default_size"] = map[string]string{"cores": "bad", "memory_mib": "bad", "disk_mib": "bad"}
	data, err := json.Marshal(fixture)
	require.NoError(t, err)
	// When parsing repeatedly.
	for range 100 {
		_, parseErr := parseMacModel(data)
		// Then the first resource field always wins.
		require.ErrorContains(t, parseErr, "cores")
	}
}
