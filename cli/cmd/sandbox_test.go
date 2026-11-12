// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"sync"
	"testing"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/stretchr/testify/require"
)

func TestSandboxCommands_routeAndRenderJSON(t *testing.T) {
	tests := []struct {
		name     string
		args     []string
		expected []expectedRequest
		wantExit int
	}{
		{
			name: "create linux waits for running",
			args: []string{"sandbox", "create", "--image", "python:3.12", "--env", "A=B", "--label", "team=cli", "--idle-timeout", "10m", "--on-idle-timeout", "delete"},
			expected: []expectedRequest{
				{method: http.MethodPost, path: "/sandboxes", status: 202, bodyJSON: `{"image":"python:3.12","env":{"A":"B"},"labels":{"team":"cli"},"lifecycle_policy":{"idle_expiry_seconds":600,"on_idle_timeout":"delete"}}`, response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"11111111-1111-1111-1111-111111111111","name":"box","status":"pending","wait_cap_seconds":3}`},
				{method: http.MethodGet, path: "/regions", status: 200, response: fixtureJSON(map[string]any{"regions": regionCatalogFixture("region-c", "Region C", "group-a", "group-b"), "default_region_id": nil})},
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", query: "wait=running&timeout_ms=55000", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
			},
			wantExit: 0,
		},
		{
			name: "list sandboxes",
			args: []string{"sandbox", "list", "--limit", "1"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/regions", status: 200, response: fixtureJSON(map[string]any{"regions": regionCatalogFixture("region-c", "Region C", "group-a", "group-b"), "default_region_id": nil})},
				{method: http.MethodGet, path: "/sandboxes", query: "limit=1", status: 200, response: sandboxPageFixture("11111111-1111-1111-1111-111111111111", "c1")},
			},
			wantExit: 0,
		},
		{
			name:     "get sandbox",
			args:     []string{"sandbox", "get", "11111111-1111-1111-1111-111111111111"},
			expected: []expectedRequest{{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")}},
			wantExit: 0,
		},
		{
			name: "wait sandbox",
			args: []string{"sandbox", "wait", "11111111-1111-1111-1111-111111111111", "--for", "paused"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", query: "wait=paused&timeout_ms=55000", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "paused")},
			},
			wantExit: 0,
		},
		{
			name: "start macOS sandbox",
			args: []string{"sandbox", "start", "11111111-1111-1111-1111-111111111111"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "macos", "stopped")},
				macReservationsRead("region-b", "active"),
				{method: http.MethodPost, path: "/sandboxes/11111111-1111-1111-1111-111111111111/start", status: 202, response: `{"status":"pending","id":"11111111-1111-1111-1111-111111111111"}`},
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", query: "wait=running&timeout_ms=55000", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "macos", "running")},
			},
			wantExit: 0,
		},
		{
			name: "fork sandbox",
			args: []string{"sandbox", "fork", "11111111-1111-1111-1111-111111111111", "--name", "forked"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "paused")},
				{method: http.MethodPost, path: "/sandboxes/11111111-1111-1111-1111-111111111111/fork", status: 202, bodyJSON: `{"name":"forked"}`, response: `{"toolbox_proxy_url":"https://proxy.example/test-tools","forked_from":"11111111-1111-1111-1111-111111111111","status":"pending","id":"22222222-2222-2222-2222-222222222222"}`},
				{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", query: "wait=paused&timeout_ms=55000", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "linux", "paused")},
			},
			wantExit: 0,
		},
		{
			name: "update labels and lifecycle",
			args: []string{"sandbox", "update", "11111111-1111-1111-1111-111111111111", "--label", "team=next", "--idle-timeout", "15m", "--on-idle-timeout", "delete"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
				{method: http.MethodPut, path: "/sandboxes/11111111-1111-1111-1111-111111111111/labels", status: 200, bodyJSON: `{"labels":{"team":"next"}}`, response: `{"id":"11111111-1111-1111-1111-111111111111","labels":{"team":"next"}}`},
				{method: http.MethodPatch, path: "/sandboxes/11111111-1111-1111-1111-111111111111/lifecycle-policy", status: 200, bodyJSON: `{"idle_expiry_seconds":900,"on_idle_timeout":"delete"}`, response: sandboxLifecycleFixture("11111111-1111-1111-1111-111111111111", 900, "delete")},
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
			},
			wantExit: 0,
		},
		{
			name: "sandbox usage",
			args: []string{"sandbox", "usage", "11111111-1111-1111-1111-111111111111"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111/usage", status: 200, response: `{"sandbox_id":"11111111-1111-1111-1111-111111111111","usage":[]}`},
			},
			wantExit: 0,
		},
		{
			name: "preview url expire",
			args: []string{"sandbox", "port", "preview-url", "expire", "11111111-1111-1111-1111-111111111111", "8080", "tok"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
				{method: http.MethodPost, path: "/sandboxes/11111111-1111-1111-1111-111111111111/ports/8080/signed-preview-urls/revoke", status: 204, bodyJSON: `{"token":"tok"}`, response: ``},
			},
			wantExit: 0,
		},
		{
			name: "logs entrypoint",
			args: []string{"sandbox", "logs", "11111111-1111-1111-1111-111111111111", "--entrypoint"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111/logs", status: 200, response: "text/plain:hello\n"},
			},
			wantExit: 0,
		},
		{
			name: "preview url signed",
			args: []string{"sandbox", "port", "preview-url", "11111111-1111-1111-1111-111111111111", "8080", "--signed", "--ttl", "2m"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
				{method: http.MethodPost, path: "/sandboxes/11111111-1111-1111-1111-111111111111/ports/8080/signed-preview-urls", status: 201, bodyJSON: `{"expires_in_seconds":120}`, response: `{"url":"https://p.example","token":"tok","expires_at":"2026-09-13T00:10:00Z"}`},
			},
			wantExit: 0,
		},
		{
			name: "delete sandbox confirms with yes",
			args: []string{"sandbox", "delete", "11111111-1111-1111-1111-111111111111", "--yes"},
			expected: []expectedRequest{
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
				{method: http.MethodDelete, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 202, response: `{"status":"deleting","id":"11111111-1111-1111-1111-111111111111"}`},
				{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", query: "wait_exact=deleted&timeout_ms=55000", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "deleted")},
			},
			wantExit: 0,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			stdout, _, err := runCLI(t, tt.args, tt.expected)
			if tt.wantExit == 0 {
				require.NoError(t, err)
				require.NotEmpty(t, requireJSONStdout(t, stdout))
				return
			}
			require.Error(t, err)
			require.Equal(t, tt.wantExit, cerr.ExitCode(err))
		})
	}
}

// A macOS sandbox may report 0 cores and 0 MiB while pending. Human output
// describes sizing, shows its reservation when available, and includes the
// boot mode when the response sets it.
func TestSandboxDetailKV_macosSizeReservationAndBootMode(t *testing.T) {
	rows := func(detail gen.Sandbox) map[string]string {
		kv := map[string]string{}
		for _, e := range sandboxDetailKV(detail) {
			kv[e[0]] = e[1]
		}
		return kv
	}
	mode := "future-boot-mode"
	unsized := rows(gen.Sandbox{Id: "mac1", Class: "macos", Status: "pending", MacBootMode: &mode})
	require.Equal(t, "sizing on first boot", unsized["cpu"])
	require.Equal(t, "sizing on first boot", unsized["cores_max"])
	require.Equal(t, "sizing on first boot", unsized["memory_mib"])
	require.Equal(t, "-", unsized["reservation_id"])
	require.Equal(t, "future-boot-mode", unsized["mac_boot_mode"])

	reservation := "region-a:reservation-1"
	sized := rows(gen.Sandbox{Id: "mac1", Class: "macos", Status: "running", Cpu: 10, CoresMax: 10, MemoryMib: 16384, ReservationId: &reservation})
	require.Equal(t, "10", sized["cpu"])
	require.Equal(t, "10", sized["cores_max"])
	require.Equal(t, "16384", sized["memory_mib"])
	require.Equal(t, "region-a:reservation-1", sized["reservation_id"])
	require.NotContains(t, sized, "mac_boot_mode")

	linux := rows(gen.Sandbox{Id: "lin1", Class: "linux", Status: "running", Cpu: 0.5, CoresMax: 1.5, MemoryMib: 1024})
	require.NotContains(t, linux, "reservation_id")
	require.Equal(t, "0.5", linux["cpu"])
	require.Equal(t, "1.5", linux["cores_max"])
}

// startMixedCatalogServers provides a catalog where region-c uses the current
// API URL and region-a publishes a different api_base_url. It returns the home
// URL and a map recording which API answered /sandboxes.
func startMixedCatalogServers(t *testing.T) (string, map[string]int) {
	t.Helper()
	var mu sync.Mutex
	answered := map[string]int{}
	item := func(id, name, region, createdAt string) map[string]any {
		return sandboxPageItemFixture(id, "c-"+name, name, region, createdAt)
	}
	remote := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "/sandboxes", r.URL.Path)
		mu.Lock()
		answered["region-a"]++
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, fixtureJSON(map[string]any{"sandboxes": []any{
			item("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeee1", "remote-one", "region-a", "2026-09-13T00:00:01Z"),
		}}))
	}))
	t.Cleanup(remote.Close)

	home := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/regions":
			_, _ = io.WriteString(w, `{"regions":[
				{"id":"earth","name":"Earth","available":true,"parent_region_ids":[]},
				{"id":"group-a","name":"Group A","available":true,"parent_region_ids":["earth"]},
				{"id":"group-b","name":"Group B","available":true,"parent_region_ids":["earth","group-a"]},
				{"id":"region-c","name":"Region C","available":true,"parent_region_ids":["earth","group-a","group-b"]},
				{"id":"group-c","name":"Group C","available":true,"parent_region_ids":["earth"]},
				{"id":"region-a","name":"Region A","available":true,"parent_region_ids":["earth","group-c"],"api_base_url":"`+remote.URL+`"}
			]}`)
		case "/sandboxes":
			mu.Lock()
			answered["region-c"]++
			mu.Unlock()
			_, _ = io.WriteString(w, fixtureJSON(map[string]any{"sandboxes": []any{
				item("11111111-1111-1111-1111-111111111111", "home-one", "region-c", "2026-09-13T00:00:02Z"),
			}}))
		default:
			require.Fail(t, "unexpected home path "+r.URL.Path)
		}
	}))
	t.Cleanup(home.Close)
	return home.URL, answered
}

func runMixedCatalogSandboxList(t *testing.T, homeURL string, args ...string) []string {
	t.Helper()
	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	root := NewRoot(stdout, stderr)
	root.SetArgs(append([]string{"--api-url", homeURL, "--api-key", "k", "-o", "json", "sandbox", "list"}, args...))
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	require.NoError(t, root.ExecuteContext(ctx), stderr.String())

	names := []string{}
	for _, item := range requireJSONStdout(t, stdout.String())["items"].([]any) {
		names = append(names, item.(map[string]any)["name"].(string))
	}
	return names
}

func TestSandboxList_unfilteredCoversTheHomeServedRegion(t *testing.T) {
	homeURL, answered := startMixedCatalogServers(t)
	require.Equal(t, []string{"home-one", "remote-one"}, runMixedCatalogSandboxList(t, homeURL))
	require.Equal(t, map[string]int{"region-c": 1, "region-a": 1}, answered,
		"the home API answers for the regions it serves, once for all of them")
}

func TestSandboxList_regionFlagResolvesAHomeServedRegion(t *testing.T) {
	homeURL, answered := startMixedCatalogServers(t)
	require.Equal(t, []string{"home-one"}, runMixedCatalogSandboxList(t, homeURL, "--region", "region-c"))
	require.Equal(t, map[string]int{"region-c": 1}, answered)
}

func TestSandboxList_regionFlagStillRefusesAnUnlistedRegion(t *testing.T) {
	homeURL, _ := startMixedCatalogServers(t)
	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	root := NewRoot(stdout, stderr)
	root.SetArgs([]string{"--api-url", homeURL, "--api-key", "k", "-o", "json", "sandbox", "list", "--region", "unlisted-region"})
	err := root.ExecuteContext(context.Background())
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.ErrorContains(t, err, "unknown region")
	require.Empty(t, stdout.String(), "no list and no cursor for a region that does not exist")
}

func TestTrimSandboxPage_preservesDeferredRegionWithoutLocalNextCursor(t *testing.T) {
	newer := time.Date(2026, 9, 13, 0, 0, 2, 0, time.UTC)
	older := time.Date(2026, 9, 13, 0, 0, 1, 0, time.UTC)
	list := output.List[gen.SandboxListItem]{
		Items: []gen.SandboxListItem{
			{Id: "a", RegionId: "region-a", CreatedAt: &newer, Cursor: "a-c1"},
			{Id: "b", RegionId: "region-b", CreatedAt: &older},
		},
	}
	trimmed := trimSandboxPage(list, regionalPageCursor{requested: map[string]sandboxCursorState{"region-a": {}, "region-b": {}}}, 1)
	require.Len(t, trimmed.Items, 1)
	require.Equal(t, "region-a:=,region-b:", trimmed.NextCursor)

	trimmed = trimSandboxPage(list, regionalPageCursor{requested: map[string]sandboxCursorState{"region-a": {}, "region-b": {cursor: "home-c0"}}}, 1)
	require.Equal(t, "region-a:=,region-b:home-c0", trimmed.NextCursor)
}

func TestSandboxListAll_preservesBareHomeCursorAcrossPages(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "list", "--all", "--limit", "1"}, []expectedRequest{
		{method: http.MethodGet, path: "/regions", status: 200, response: fixtureJSON(map[string]any{"regions": regionCatalogFixture("region-c", "Region C", "group-a", "group-b"), "default_region_id": nil})},
		{method: http.MethodGet, path: "/sandboxes", query: "limit=1", status: 200, response: fixtureJSON(map[string]any{"sandboxes": []any{sandboxPageItemFixture("11111111-1111-1111-1111-111111111111", "home-c1", "one", "region-c", "2026-09-13T00:00:02Z")}, "next_cursor": "home-c1"})},
		{method: http.MethodGet, path: "/sandboxes", query: "cursor=home-c1&limit=1", status: 200, response: fixtureJSON(map[string]any{"sandboxes": []any{sandboxPageItemFixture("22222222-2222-2222-2222-222222222222", "home-c2", "two", "region-c", "2026-09-13T00:00:01Z")}})},
	})
	require.NoError(t, err)
	items := requireJSONStdout(t, stdout)["items"].([]any)
	require.Len(t, items, 2)
}

// A bare requested cursor (carried over from a single API endpoint, or
// hand-typed) during a multi-region fan-out must NOT collapse the fan-out's
// per-region continuation onto "": the next cursor keys each region on its
// own server cursor, so page two is not page one again.
func TestTrimSandboxPage_bareRequestedCursorDuringFanOutKeepsRegionCursors(t *testing.T) {
	newer := time.Date(2026, 9, 13, 0, 0, 2, 0, time.UTC)
	older := time.Date(2026, 9, 13, 0, 0, 1, 0, time.UTC)
	list := output.List[gen.SandboxListItem]{
		Items: []gen.SandboxListItem{
			{Id: "a", RegionId: "region-b", CreatedAt: &newer, Cursor: "b-c1"},
			{Id: "b", RegionId: "region-a", CreatedAt: &older, Cursor: "a-c1"},
		},
		NextCursor: "region-b:c1,region-a:c2",
	}
	trimmed := trimSandboxPage(list, regionalPageCursor{requested: map[string]sandboxCursorState{"": {cursor: "abc"}}}, 0)
	require.Equal(t, "region-a:a-c1,region-b:b-c1", trimmed.NextCursor)
	require.NotContains(t, trimmed.NextCursor, "abc", "the stale bare cursor must not be re-emitted per region")
}

func TestSandboxDetailKV_macosPersistence(t *testing.T) {
	for _, persistent := range []bool{true, false} {
		t.Run(strconv.FormatBool(persistent), func(t *testing.T) {
			// Given
			detail := gen.Sandbox{Class: "macos", Persistent: &persistent}
			// When
			rows := sandboxDetailKV(detail)
			// Then
			require.Contains(t, rows, [2]string{"persistent", strconv.FormatBool(persistent)})
		})
	}
}

func TestTrimSandboxPage_failedLegKeepsItsRequestedCursorWhileTheListContinues(t *testing.T) {
	list := output.List[gen.SandboxListItem]{
		Items:      []gen.SandboxListItem{{RegionId: "region-b", Cursor: "b2"}},
		NextCursor: "region-b:b2",
		Incomplete: true,
		Errors:     []output.RegionError{{Region: "region-a", Message: "boom"}},
	}
	requested := map[string]sandboxCursorState{"region-b": {cursor: "b1"}, "region-a": {cursor: "a5"}}
	trimmed := trimSandboxPage(list, regionalPageCursor{requested: requested}, 0)
	require.Equal(t, "region-a:a5,region-b:b2", trimmed.NextCursor)
}
