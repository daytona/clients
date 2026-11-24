// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"math"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestAccountLimitsParsesSandboxTimeoutDurations(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /orgs/org-id/limits": jsonHandler(t, accountLimitsFixture()),
	})
	client := newTestClient(t, fake.Server)
	// When
	result, err := client.Limits(t.Context(), &GetLimitsOptions{OrganizationID: "org-id"})
	// Then
	require.NoError(t, err)
	require.Equal(t, SandboxTimeoutLimits{Max: time.Hour, Ceiling: 24 * time.Hour}, result.SandboxTimeout)
}

func TestAccountLimitsRejectsMalformedSandboxTimeout(t *testing.T) {
	for _, value := range []any{
		nil, "invalid",
		map[string]any{},
		map[string]any{"max_seconds": 1},
		map[string]any{"ceiling_seconds": 1},
		map[string]any{"max_seconds": 1.5, "ceiling_seconds": 2},
		map[string]any{"max_seconds": 1, "ceiling_seconds": "2"},
		map[string]any{"max_seconds": math.MaxInt64, "ceiling_seconds": 2},
		map[string]any{"max_seconds": 1, "ceiling_seconds": math.MinInt64},
	} {
		t.Run("", func(t *testing.T) {
			// Given a malformed required timeout object.
			fixture := accountLimitsFixture()
			fixture["sandbox_timeout"] = value
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /orgs/org-id/limits": jsonHandler(t, fixture),
			})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Limits(t.Context(), &GetLimitsOptions{OrganizationID: "org-id"})
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Contains(t, err.Error(), "sandbox_timeout")
		})
	}
}

func TestAccountLimitsRejectsMissingSandboxTimeout(t *testing.T) {
	// Given
	fixture := accountLimitsFixture()
	delete(fixture, "sandbox_timeout")
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /orgs/org-id/limits": jsonHandler(t, fixture),
	})
	client := newTestClient(t, fake.Server)
	// When
	_, err := client.Limits(t.Context(), &GetLimitsOptions{OrganizationID: "org-id"})
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
}
