// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestRegionalLists_querySharedURLOnceAndDeduplicateIDs(t *testing.T) {
	for _, resource := range []string{"sandbox", "image"} {
		t.Run(resource, func(t *testing.T) {
			// Given three catalog regions with equivalent URLs and repeated IDs.
			var calls atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				writeListItems(t, w, resource, []listTestItem{{"one", "west", "c1"}, {"one", "west", "c1"}, {"two", "east", "c2"}}, "")
			}))
			defer server.Close()
			catalog := []gen.Region{
				listTestRegion("east", server.URL),
				listTestRegion("north", "  "+server.URL+"/  "),
				listTestRegion("west", server.URL+"///"),
			}
			// When
			out, err := executeRegionalList(t, resource, catalog, nil)
			// Then
			require.NoError(t, err)
			require.Equal(t, int32(1), calls.Load())
			require.Len(t, out.Items, 2)
			require.ElementsMatch(t, []string{"one", "two"}, []string{out.Items[0].ID, out.Items[1].ID})
			require.Equal(t, "west", out.Items[0].Region)
			require.Equal(t, "east", out.Items[1].Region)
		})
	}
}

func TestRegionalLists_sharedURLCursorUsesLastEmittedRowAcrossRegions(t *testing.T) {
	for _, resource := range []string{"sandbox", "image"} {
		t.Run(resource, func(t *testing.T) {
			// Given one API endpoint serving several regions, with a truncated terminal page.
			var first, resumed atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Query().Get("cursor") {
				case "":
					first.Add(1)
					writeListItems(t, w, resource, []listTestItem{{"one", "west", "c1"}, {"two", "north", "c2"}}, "")
				case "c1":
					resumed.Add(1)
					writeListItems(t, w, resource, []listTestItem{{"two", "north", "c2"}}, "")
				default:
					t.Errorf("unexpected cursor %q", r.URL.Query().Get("cursor"))
					w.WriteHeader(http.StatusBadRequest)
				}
			}))
			defer server.Close()
			catalog := []gen.Region{listTestRegion("east", server.URL), listTestRegion("north", server.URL+"/"), listTestRegion("west", server.URL)}
			// When
			out, err := executeRegionalList(t, resource, catalog, []string{"--all", "--limit", "1"})
			// Then
			require.NoError(t, err)
			require.Len(t, out.Items, 2)
			require.Equal(t, "one", out.Items[0].ID)
			require.Equal(t, "two", out.Items[1].ID)
			require.Equal(t, int32(1), first.Load())
			require.Equal(t, int32(1), resumed.Load())
			require.Empty(t, out.NextCursor)
		})
	}
}

func TestRegionalLists_multipleURLsDeduplicateAndPreservePartialExit(t *testing.T) {
	for _, resource := range []string{"sandbox", "image"} {
		t.Run(resource, func(t *testing.T) {
			// Given two reachable API endpoints returning the same ID and one failing endpoint.
			good := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				writeListItems(t, w, resource, []listTestItem{{"one", "west", "c1"}}, "c1")
			}))
			defer good.Close()
			other := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				writeListItems(t, w, resource, []listTestItem{{"one", "west", "c1"}}, "c1")
			}))
			defer other.Close()
			bad := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(http.StatusBadGateway)
			}))
			defer bad.Close()
			catalog := []gen.Region{listTestRegion("east", good.URL), listTestRegion("west", other.URL), listTestRegion("north", bad.URL)}
			// When
			out, err := executeRegionalList(t, resource, catalog, nil)
			// Then
			require.Equal(t, cerr.ExitPartial, cerr.ExitCode(err))
			require.True(t, out.Incomplete)
			require.Len(t, out.Items, 1)
			require.Len(t, out.Errors, 1)
			require.Equal(t, "north", out.Errors[0].Region)
			require.NotEmpty(t, out.NextCursor)
		})
	}
}

type listTestItem struct{ id, region, cursor string }

type listTestOutput struct {
	Items []struct {
		ID     string `json:"id"`
		Region string `json:"region_id"`
	} `json:"items"`
	NextCursor string `json:"next_cursor"`
	Incomplete bool   `json:"incomplete"`
	Errors     []struct {
		Region string `json:"region"`
	} `json:"errors"`
}

func listTestRegion(id, base string) gen.Region {
	return gen.Region{Id: id, ApiBaseUrl: &base}
}

func writeListItems(t *testing.T, w http.ResponseWriter, resource string, resources []listTestItem, next string) {
	t.Helper()
	items := make([]map[string]string, 0, len(resources))
	for _, item := range resources {
		created := "2026-09-13T00:00:02Z"
		if item.cursor == "c2" {
			created = "2026-09-13T00:00:01Z"
		}
		if item.cursor == "c3" {
			created = "2026-09-13T00:00:00Z"
		}
		items = append(items, map[string]string{"id": item.id, "region_id": item.region, "cursor": item.cursor, "created_at": created})
	}
	plural := resource + "s"
	if resource == "sandbox" {
		plural = "sandboxes"
	}
	if err := json.NewEncoder(w).Encode(map[string]any{plural: items, "next_cursor": next}); err != nil {
		t.Error(err)
	}
}

func executeRegionalList(t *testing.T, resource string, catalog []gen.Region, args []string) (listTestOutput, error) {
	t.Helper()
	home := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if writeSearchNotConfigured(w, r) {
			return
		}
		if r.URL.Path != "/regions" {
			t.Errorf("unexpected home path %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
			return
		}
		if err := json.NewEncoder(w).Encode(gen.RegionList{Regions: catalog}); err != nil {
			t.Error(err)
		}
	}))
	defer home.Close()
	stdout, stderr := &bytes.Buffer{}, &bytes.Buffer{}
	root := NewRoot(stdout, stderr)
	root.SetArgs(append([]string{"--api-url", home.URL, "--api-key", "k", "-o", "json", resource, "list"}, args...))
	err := root.ExecuteContext(context.Background())
	var out listTestOutput
	require.NoError(t, json.Unmarshal(stdout.Bytes(), &out), "stderr: %s; command error: %v", stderr, err)
	return out, err
}

// writeSearchNotConfigured answers a search route as a deployment without
// search does (501 SEARCH_NOT_CONFIGURED), so a list falls back to its
// regional list routes. It reports whether r was a search route.
func writeSearchNotConfigured(w http.ResponseWriter, r *http.Request) bool {
	if !strings.HasSuffix(r.URL.Path, "/search") {
		return false
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusNotImplemented)
	_, _ = io.WriteString(w, `{"code":"SEARCH_NOT_CONFIGURED","message":"search is not configured","source":"DAYTONA_API"}`)
	return true
}

// searchNotConfiguredRequest is a runCLI expectation for the search route of
// a deployment without search: the list then falls back to its list route.
func searchNotConfiguredRequest(path string) expectedRequest {
	return expectedRequest{method: http.MethodGet, path: path, status: http.StatusNotImplemented, response: `{"code":"SEARCH_NOT_CONFIGURED","message":"search is not configured","source":"DAYTONA_API"}`}
}

// withSearchTotals turns a list page fixture into the search route's page: the
// same items and cursor, plus the total the search contract requires.
func withSearchTotals(t *testing.T, page string) string {
	t.Helper()
	var body map[string]any
	require.NoError(t, json.Unmarshal([]byte(page), &body))
	// The total is the page's own item count, so the fixture stays a
	// consistent search response whatever it holds.
	total := 0
	for _, key := range []string{"sandboxes", "images", "snapshots"} {
		if items, ok := body[key].([]any); ok {
			total = len(items)
		}
	}
	body["total"] = total
	body["total_relation"] = "eq"
	encoded, err := json.Marshal(body)
	require.NoError(t, err)
	return string(encoded)
}

// withNextCursor sets a page fixture's next_cursor (nil ends the list).
func withNextCursor(t *testing.T, page string, cursor any) string {
	t.Helper()
	var body map[string]any
	require.NoError(t, json.Unmarshal([]byte(page), &body))
	body["next_cursor"] = cursor
	encoded, err := json.Marshal(body)
	require.NoError(t, err)
	return string(encoded)
}
