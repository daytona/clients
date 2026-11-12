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
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestSandboxListAll_zeroActiveRegionsAdvancesBareHomeCursor(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "list", "--all", "--limit", "1"}, []expectedRequest{searchNotConfiguredRequest("/sandboxes/search"),
		{method: http.MethodGet, path: "/regions", status: 200, response: `{"regions":[],"default_region_id":null}`},
		{method: http.MethodGet, path: "/sandboxes", query: "limit=1", status: 200, response: fixtureJSON(map[string]any{"sandboxes": []any{sandboxPageItemFixture("11111111-1111-1111-1111-111111111111", "home-c1", "one", "region-c", "2026-09-13T00:00:03Z")}, "next_cursor": "home-c1"})},
		{method: http.MethodGet, path: "/sandboxes", query: "cursor=home-c1&limit=1", status: 200, response: fixtureJSON(map[string]any{"sandboxes": []any{sandboxPageItemFixture("22222222-2222-2222-2222-222222222222", "home-c2", "two", "region-c", "2026-09-13T00:00:02Z")}, "next_cursor": "home-c2"})},
		{method: http.MethodGet, path: "/sandboxes", query: "cursor=home-c2&limit=1", status: 200, response: fixtureJSON(map[string]any{"sandboxes": []any{sandboxPageItemFixture("33333333-3333-3333-3333-333333333333", "home-c3", "three", "region-c", "2026-09-13T00:00:01Z")}})},
	})
	require.NoError(t, err)
	items := requireJSONStdout(t, stdout)["items"].([]any)
	require.Len(t, items, 3)
}

// One API serving SEVERAL listed regions is one fan-out leg, named by its most
// specific region, while its items retain their own region IDs. Trimming
// tracks each request's cursor so that an item deferred by the page limit
// returns on the next page, even when its region differs from the cursor key.
func multiRegionHomeLegServers(t *testing.T) string {
	t.Helper()
	remote := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// The CLI probes search on the home API only: a search request here
		// is a bug, and the path assertion fails it.
		require.Equal(t, "/sandboxes", r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Query().Get("cursor") {
		case "":
			_, _ = io.WriteString(w, fixtureJSON(map[string]any{
				"sandboxes":   []any{sandboxPageItemFixture("33333333-3333-3333-3333-333333333333", "r1", "remote-one", "region-0", "2026-09-13T00:00:04Z")},
				"next_cursor": "r1",
			}))
		case "r1":
			_, _ = io.WriteString(w, fixtureJSON(map[string]any{
				"sandboxes": []any{sandboxPageItemFixture("44444444-4444-4444-4444-444444444444", "r2", "remote-two", "region-0", "2026-09-13T00:00:02Z")},
			}))
		default:
			require.Fail(t, "unexpected remote cursor "+r.URL.Query().Get("cursor"))
		}
	}))
	t.Cleanup(remote.Close)

	regionB := sandboxPageItemFixture("22222222-2222-2222-2222-222222222222", "h2", "home-two", "region-b", "2026-09-13T00:00:03Z")
	home := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if writeSearchNotConfigured(w, r) {
			return
		}
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/regions":
			// `region-a` and `region-b` publish no URL, so ONE home
			// leg answers for both; only `region-0` is a second API.
			_, _ = io.WriteString(w, fixtureJSON(map[string]any{"regions": []any{
				map[string]any{"id": "earth", "name": "Earth", "available": true, "parent_region_ids": []string{}},
				map[string]any{"id": "region-0", "name": "Region 0", "available": true, "parent_region_ids": []string{"earth"}, "api_base_url": remote.URL},
				map[string]any{"id": "region-a", "name": "Region A", "available": true, "parent_region_ids": []string{"earth"}},
				map[string]any{"id": "region-b", "name": "Region B", "available": true, "parent_region_ids": []string{"earth"}},
			}}))
		case "/sandboxes":
			switch r.URL.Query().Get("cursor") {
			case "":
				// No next_cursor: the leg is exhausted, so an item trimmed off
				// this page can only come back through the leg's own cursor.
				_, _ = io.WriteString(w, fixtureJSON(map[string]any{"sandboxes": []any{
					sandboxPageItemFixture("11111111-1111-1111-1111-111111111111", "h1", "home-one", "region-a", "2026-09-13T00:00:05Z"),
					regionB,
				}}))
			case "h1":
				_, _ = io.WriteString(w, fixtureJSON(map[string]any{"sandboxes": []any{regionB}}))
			default:
				require.Fail(t, "unexpected home cursor "+r.URL.Query().Get("cursor"))
			}
		default:
			require.Fail(t, "unexpected home path "+r.URL.Path)
		}
	}))
	t.Cleanup(home.Close)
	return home.URL
}

func TestSandboxListAll_oneAPIServingSeveralRegionsLosesNoTrimmedRow(t *testing.T) {
	require.Equal(t,
		[]string{"home-one", "remote-one", "home-two", "remote-two"},
		runMixedCatalogSandboxList(t, multiRegionHomeLegServers(t), "--all", "--limit", "2"),
		"every item once: the trimmed region-b item returns through its request's cursor")
}

// A leg that answers an EMPTY page with no continuation is EXHAUSTED, and the
// merged cursor must say so (`<leg>:=`). Left out of it, the leg is absent
// from the next request's cursor too, so the fan-out asks it from the start
// again while the other leg advances — and every item it already returned comes
// back a second time.
//
// The home leg below runs out one page before the remote one, which is what
// makes the restart observable: it has a first page to repeat.
func exhaustedLegServers(t *testing.T) (string, func() []string) {
	t.Helper()
	remote := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// The CLI probes search on the home API only: a search request here
		// is a bug, and the path assertion fails it.
		require.Equal(t, "/sandboxes", r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		switch cursor := r.URL.Query().Get("cursor"); cursor {
		case "":
			_, _ = io.WriteString(w, fixtureJSON(map[string]any{
				"sandboxes":   []any{sandboxPageItemFixture("33333333-3333-3333-3333-333333333333", "r1", "remote-one", "region-0", "2026-09-13T00:00:04Z")},
				"next_cursor": "r1",
			}))
		case "r1":
			_, _ = io.WriteString(w, fixtureJSON(map[string]any{
				"sandboxes":   []any{sandboxPageItemFixture("44444444-4444-4444-4444-444444444444", "r2", "remote-two", "region-0", "2026-09-13T00:00:02Z")},
				"next_cursor": "r2",
			}))
		case "r2":
			_, _ = io.WriteString(w, fixtureJSON(map[string]any{
				"sandboxes": []any{sandboxPageItemFixture("55555555-5555-5555-5555-555555555555", "r3", "remote-three", "region-0", "2026-09-13T00:00:01Z")},
			}))
		default:
			require.Fail(t, "unexpected remote cursor "+cursor)
		}
	}))
	t.Cleanup(remote.Close)

	var mu sync.Mutex
	asked := []string{}
	home := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if writeSearchNotConfigured(w, r) {
			return
		}
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/regions":
			_, _ = io.WriteString(w, fixtureJSON(map[string]any{"regions": []any{
				map[string]any{"id": "earth", "name": "Earth", "available": true, "parent_region_ids": []string{}},
				map[string]any{"id": "region-0", "name": "Region 0", "available": true, "parent_region_ids": []string{"earth"}, "api_base_url": remote.URL},
				map[string]any{"id": "region-c", "name": "Region C", "available": true, "parent_region_ids": []string{"earth"}},
			}}))
		case "/sandboxes":
			cursor := r.URL.Query().Get("cursor")
			mu.Lock()
			asked = append(asked, cursor)
			mu.Unlock()
			switch cursor {
			case "":
				_, _ = io.WriteString(w, fixtureJSON(map[string]any{
					"sandboxes":   []any{sandboxPageItemFixture("11111111-1111-1111-1111-111111111111", "h1", "home-one", "region-c", "2026-09-13T00:00:05Z")},
					"next_cursor": "h1",
				}))
			case "h1":
				_, _ = io.WriteString(w, fixtureJSON(map[string]any{"sandboxes": []any{}}))
			default:
				require.Fail(t, "unexpected home cursor "+cursor)
			}
		default:
			require.Fail(t, "unexpected home path "+r.URL.Path)
		}
	}))
	t.Cleanup(home.Close)
	return home.URL, func() []string {
		mu.Lock()
		defer mu.Unlock()
		return append([]string(nil), asked...)
	}
}

func TestSandboxListAll_exhaustedLegIsNotQueriedAgain(t *testing.T) {
	homeURL, homeCursors := exhaustedLegServers(t)
	require.Equal(t,
		[]string{"home-one", "remote-one", "remote-two", "remote-three"},
		runMixedCatalogSandboxList(t, homeURL, "--all", "--limit", "2"),
		"every item once: the leg that ran out is never asked from the start again")
	require.Equal(t, []string{"", "h1"}, homeCursors(),
		"the home leg answers its first page and the empty one that exhausts it — the pages after that skip it")
}

func TestSandboxList_exhaustedLegIsMarkedDoneInTheMergedCursor(t *testing.T) {
	homeURL, _ := exhaustedLegServers(t)
	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	root := NewRoot(stdout, stderr)
	root.SetArgs([]string{"--api-url", homeURL, "--api-key", "k", "-o", "json", "sandbox", "list",
		"--limit", "2", "--cursor", "region-0:r1,region-c:h1"})
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	require.NoError(t, root.ExecuteContext(ctx), stderr.String())

	var page struct {
		NextCursor string `json:"next_cursor"`
	}
	require.NoError(t, json.Unmarshal(stdout.Bytes(), &page))
	require.Equal(t, "region-0:r2,region-c:=", page.NextCursor,
		"the exhausted leg keeps its done marker in the cursor the caller is handed back")
}

func TestSandboxListAll_oneAPIServingSeveralRegionsResumesByLeg(t *testing.T) {
	homeURL := multiRegionHomeLegServers(t)
	stdout := &bytes.Buffer{}
	root := NewRoot(stdout, &bytes.Buffer{})
	root.SetArgs([]string{"--api-url", homeURL, "--api-key", "k", "-o", "json", "sandbox", "list", "--limit", "2"})
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	require.NoError(t, root.ExecuteContext(ctx))

	var page struct {
		NextCursor string `json:"next_cursor"`
	}
	require.NoError(t, json.Unmarshal(stdout.Bytes(), &page))
	require.Equal(t, "region-0:r1,region-a:h1", page.NextCursor,
		"the home request resumes after its last kept item, with its own cursor key")
}
