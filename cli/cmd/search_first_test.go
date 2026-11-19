// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"errors"
	"net/http"
	"strings"
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

func TestImageList_regionFlagKeepsSearchItemsAvailableInTheRegion(t *testing.T) {
	// Given a deployment with search and two images homed outside region-c,
	// one of them also available in it. The search route's region_ids
	// matches the home region only, so --region is not sent to it.
	regions := fixtureJSON(map[string]any{"regions": regionCatalogFixture("region-c", "Region C", "group-a", "group-b"), "default_region_id": nil})
	available := imageRegionItemFixture(`{"id":"11111111-1111-1111-1111-111111111111","name":"available","region_id":"region-root","created_at":"2026-09-14T00:00:02Z","updated_at":"2026-09-14T00:00:02Z","cursor":"c1"}`)
	available = strings.Replace(available, `"region_ids":["region-b"]`, `"region_ids":["region-root","region-c"]`, 1)
	elsewhere := imageRegionItemFixture(`{"id":"22222222-2222-2222-2222-222222222222","name":"elsewhere","region_id":"region-root","created_at":"2026-09-14T00:00:01Z","updated_at":"2026-09-14T00:00:01Z","cursor":"c2"}`)
	elsewhere = strings.Replace(elsewhere, `"region_ids":["region-b"]`, `"region_ids":["region-root"]`, 1)
	page := `{"images":[` + available + `,` + elsewhere + `]}`
	// When
	stdout, _, err := runCLI(t, []string{"image", "list", "--region", "region-c", "--limit", "2"}, []expectedRequest{
		{method: http.MethodGet, path: "/regions", status: 200, response: regions},
		{method: http.MethodGet, path: "/images/search", query: "limit=2", status: 200, response: withSearchTotals(t, page)},
	})
	// Then: the image homed elsewhere but available in region-c is kept.
	require.NoError(t, err)
	items, ok := requireJSONStdout(t, stdout)["items"].([]any)
	require.True(t, ok)
	require.Len(t, items, 1)
	require.Equal(t, "11111111-1111-1111-1111-111111111111", items[0].(map[string]any)["id"])
}

func TestImageList_unknownRegionIsRefusedBeforeSearching(t *testing.T) {
	// Given
	regions := fixtureJSON(map[string]any{"regions": regionCatalogFixture("region-c", "Region C", "group-a", "group-b"), "default_region_id": nil})
	// When
	_, _, err := runCLI(t, []string{"image", "list", "--region", "nowhere"}, []expectedRequest{
		{method: http.MethodGet, path: "/regions", status: 200, response: regions},
	})
	// Then
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestArtifactGet_searchIsRefusedAsAName(t *testing.T) {
	for _, kind := range []string{"image", "snapshot"} {
		t.Run(kind, func(t *testing.T) {
			// Given / When: no request is expected.
			_, _, err := runCLI(t, []string{kind, "get", "search"}, nil)
			// Then
			require.Error(t, err)
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
			require.Contains(t, err.Error(), "'search' cannot be looked up by name")
			require.Contains(t, err.Error(), "use the "+kind+" ID")
		})
	}
}

func TestSecretAndVolumeLists_searchFirstFallbacks(t *testing.T) {
	secret := `{"name":"TOKEN","allowed_domains":["example.com"],"created_at":"2026-09-01T00:00:00Z","updated_at":"2026-09-01T00:00:00Z","cursor":"sc"}`
	volume := diskVolumeSearchFixture("vol_1")
	for _, tc := range []struct {
		name  string
		field string
		item  string
	}{
		{name: "secret", field: "secrets", item: secret},
		{name: "volume", field: "volumes", item: volume},
	} {
		t.Run(tc.name+" search", func(t *testing.T) {
			stdout, _, err := runCLI(t, []string{tc.name, "list", "--limit", "1"}, []expectedRequest{{method: http.MethodGet, path: "/" + tc.field + "/search", query: "limit=1", status: http.StatusOK, response: `{"` + tc.field + `":[` + tc.item + `],"total":1,"total_relation":"eq"}`}})
			require.NoError(t, err)
			require.Len(t, requireJSONStdout(t, stdout)["items"], 1)
		})
		t.Run(tc.name+" 501 fallback", func(t *testing.T) {
			_, _, err := runCLI(t, []string{tc.name, "list"}, []expectedRequest{
				searchNotConfiguredRequest("/" + tc.field + "/search"),
				{method: http.MethodGet, path: "/" + tc.field, query: "limit=100", status: http.StatusOK, response: `{"` + tc.field + `":[` + tc.item + `]}`},
			})
			require.NoError(t, err)
		})
		t.Run(tc.name+" 404 fallback", func(t *testing.T) {
			_, _, err := runCLI(t, []string{tc.name, "list"}, []expectedRequest{
				{method: http.MethodGet, path: "/" + tc.field + "/search", query: "limit=100", status: http.StatusNotFound, response: `{"code":"NOT_FOUND","message":"not found","source":"DAYTONA_API"}`, skipContract: true},
				{method: http.MethodGet, path: "/" + tc.field, query: "limit=100", status: http.StatusOK, response: `{"` + tc.field + `":[` + tc.item + `]}`},
			})
			require.NoError(t, err)
		})
		t.Run(tc.name+" old item fallback", func(t *testing.T) {
			_, _, err := runCLI(t, []string{tc.name, "list"}, []expectedRequest{
				{method: http.MethodGet, path: "/" + tc.field + "/search", query: "limit=100", status: http.StatusOK, response: tc.item, skipContract: true},
				{method: http.MethodGet, path: "/" + tc.field, query: "limit=100", status: http.StatusOK, response: `{"` + tc.field + `":[` + tc.item + `]}`},
			})
			require.NoError(t, err)
		})
		t.Run(tc.name+" 503", func(t *testing.T) {
			_, _, err := runCLI(t, []string{tc.name, "list"}, []expectedRequest{{method: http.MethodGet, path: "/" + tc.field + "/search", query: "limit=100", status: http.StatusServiceUnavailable, response: `{"code":"SEARCH_UNAVAILABLE","message":"search unavailable","source":"DAYTONA_API"}`}})
			require.Error(t, err)
			require.Equal(t, cerr.ExitFailure, cerr.ExitCode(err))
		})
		t.Run(tc.name+" remembered list 410 retries search", func(t *testing.T) {
			stdout, _, err := runCLI(t, []string{tc.name, "list", "--all", "--limit", "1"}, []expectedRequest{
				searchNotConfiguredRequest("/" + tc.field + "/search"),
				{method: http.MethodGet, path: "/" + tc.field, query: "limit=1", status: http.StatusOK, response: `{"` + tc.field + `":[` + tc.item + `],"next_cursor":"p2"}`},
				{method: http.MethodGet, path: "/" + tc.field, query: "cursor=p2&limit=1", status: http.StatusGone, response: `{"code":"GONE","message":"list retired","source":"DAYTONA_API","replacement":"/` + tc.field + `/search"}`},
				{method: http.MethodGet, path: "/" + tc.field + "/search", query: "cursor=p2&limit=1", status: http.StatusOK, response: `{"` + tc.field + `":[` + tc.item + `],"total":1,"total_relation":"eq"}`},
			})
			require.NoError(t, err)
			require.Len(t, requireJSONStdout(t, stdout)["items"], 2)
		})
	}
}

func TestSecretAndVolume_searchNameIsRefusedBeforeARequest(t *testing.T) {
	for _, args := range [][]string{
		{"secret", "get", "search"},
		{"secret", "set", "search", "--value", "value", "--allowed-domain", "example.com"},
		{"volume", "get", "search"},
		{"volume", "create", "search"},
	} {
		_, _, err := runCLI(t, args, nil)
		require.Error(t, err)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
		require.Contains(t, err.Error(), "reserved for the search route")
	}
}
