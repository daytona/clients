// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

// The API stopped returning the organization limits on total sandboxes and the
// storage counts (contract OrganizationLimits): those entries carry usage only.
func TestAccountLimits_whenLimitIsNotReturned_keepsUsageWithNilLimit(t *testing.T) {
	// Given a response whose deprecated counts carry a null limit
	body := accountLimitsFixture()
	usageOnly := func() map[string]any { return map[string]any{"limit": nil, "usage": 7} }
	body["sandboxes"] = map[string]any{"concurrent": accountUsageFixture(), "total": usageOnly()}
	body["storage"] = map[string]any{"volumes": usageOnly(), "images": usageOnly(), "snapshots": usageOnly()}
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /orgs/org-id/limits": jsonHandler(t, body)})
	client := newTestClient(t, fake.Server)
	// When
	result, err := client.Limits(t.Context(), &GetLimitsOptions{OrganizationID: "org-id"})
	// Then
	require.NoError(t, err)
	notReturned := UsageLimit{Usage: 7}
	require.Equal(t, notReturned, result.Sandboxes.Total)
	require.Equal(t, StorageLimits{Volumes: notReturned, Images: notReturned, Snapshots: notReturned}, result.Storage)
	require.NotNil(t, result.Sandboxes.Concurrent.Limit)
}
