// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"errors"
	"net/http"
	"testing"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestSearchNotServed_only501SearchNotConfiguredOr404(t *testing.T) {
	// Given / When / Then
	require.True(t, searchNotServed(&api.APIError{Status: http.StatusNotImplemented, Code: "SEARCH_NOT_CONFIGURED"}))
	require.True(t, searchNotServed(&api.APIError{Status: http.StatusNotFound, Code: "NOT_FOUND"}))
	require.False(t, searchNotServed(&api.APIError{Status: http.StatusServiceUnavailable, Code: "SEARCH_UNAVAILABLE"}))
	require.False(t, searchNotServed(&api.APIError{Status: http.StatusNotImplemented, Code: "NOT_IMPLEMENTED"}))
	require.False(t, searchNotServed(&api.APIError{Status: http.StatusGone, Code: "GONE"}))
	require.False(t, searchNotServed(errors.New("plain")))
}

func TestSandboxList_regionFlagIsTheSearchRegionFilter(t *testing.T) {
	// Given a deployment with search and a known region.
	regions := fixtureJSON(map[string]any{"regions": regionCatalogFixture("region-c", "Region C", "group-a", "group-b"), "default_region_id": nil})
	// When
	stdout, _, err := runCLI(t, []string{"sandbox", "list", "--region", "region-c", "--limit", "1"}, []expectedRequest{
		{method: http.MethodGet, path: "/regions", status: 200, response: regions},
		{method: http.MethodGet, path: "/sandboxes/search", query: "limit=1&region_ids=region-c", status: 200, response: withSearchTotals(t, fixtureJSON(map[string]any{"sandboxes": []any{sandboxPageItemFixture("11111111-1111-1111-1111-111111111111", "c1", "one", "region-c", "2026-09-13T00:00:02Z")}}))},
	})
	// Then
	require.NoError(t, err)
	require.Len(t, requireJSONStdout(t, stdout)["items"], 1)
}

func TestSandboxList_unknownRegionIsRefusedBeforeSearching(t *testing.T) {
	// Given
	regions := fixtureJSON(map[string]any{"regions": regionCatalogFixture("region-c", "Region C", "group-a", "group-b"), "default_region_id": nil})
	// When
	_, _, err := runCLI(t, []string{"sandbox", "list", "--region", "nowhere"}, []expectedRequest{
		{method: http.MethodGet, path: "/regions", status: 200, response: regions},
	})
	// Then
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestSnapshotListAll_probesSearchOnceOnADeploymentWithoutSearch(t *testing.T) {
	// Given a deployment without search and two snapshot pages.
	// When
	stdout, _, err := runCLI(t, []string{"snapshot", "list", "--all", "--limit", "1"}, []expectedRequest{
		searchNotConfiguredRequest("/snapshots/search"),
		{method: http.MethodGet, path: "/snapshots", query: "limit=1", status: 200, response: withNextCursor(t, snapshotPageFixture("33333333-3333-3333-3333-333333333333"), "p2")},
		{method: http.MethodGet, path: "/snapshots", query: "limit=1&cursor=p2", status: 200, response: withNextCursor(t, snapshotPageFixture("44444444-4444-4444-4444-444444444444"), nil)},
	})
	// Then: the second page went straight to the list route.
	require.NoError(t, err)
	require.Len(t, requireJSONStdout(t, stdout)["items"], 2)
}
