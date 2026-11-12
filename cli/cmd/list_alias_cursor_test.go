// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

type aliasListItem struct {
	ID        string    `json:"id"`
	Region    string    `json:"region_id"`
	Cursor    string    `json:"cursor"`
	CreatedAt time.Time `json:"created_at"`
}

type aliasListResult struct {
	Items      []aliasListItem `json:"items"`
	Next       string          `json:"next_cursor"`
	Incomplete bool            `json:"incomplete"`
}

type aliasListFixture struct {
	home              string
	mu                sync.Mutex
	requests          map[string][]string
	firstAliasVisible bool
}

// A stable two-endpoint HTTP fixture exercises alias cursor pagination.
func aliasListServer(t *testing.T, failSecond bool) *aliasListFixture {
	t.Helper()
	f := &aliasListFixture{requests: map[string][]string{}, firstAliasVisible: true}
	items := map[string][]aliasListItem{
		"a": {{"a", "region-a", "6", time.Unix(6, 0).UTC()}, {"shared", "region-b", "4", time.Unix(4, 0).UTC()}, {"c", "region-c", "2", time.Unix(2, 0).UTC()}},
		"b": {{"b", "region-d", "5", time.Unix(5, 0).UTC()}, {"shared", "region-d", "4", time.Unix(4, 0).UTC()}, {"d", "region-d", "1", time.Unix(1, 0).UTC()}},
	}
	serveItems := func(endpoint string, w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		f.requests[endpoint] = append(f.requests[endpoint], r.URL.RawQuery)
		f.mu.Unlock()
		if failSecond && endpoint == "b" {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		limit := 100
		if raw := r.URL.Query().Get("limit"); raw != "" {
			n, err := strconv.Atoi(raw)
			require.NoError(t, err)
			limit = min(n, 200)
		}
		cursor := 100
		if raw := r.URL.Query().Get("cursor"); raw != "" {
			n, err := strconv.Atoi(raw)
			require.NoError(t, err)
			cursor = n
		}
		out := []aliasListItem{}
		for _, item := range items[endpoint] {
			if item.CreatedAt.Unix() < int64(cursor) {
				out = append(out, item)
			}
		}
		var next *string
		if len(out) > limit {
			out = out[:limit]
			next = &out[len(out)-1].Cursor
		}
		plural := "sandboxes"
		if strings.HasSuffix(r.URL.Path, "images") {
			plural = "images"
		}
		require.NoError(t, json.NewEncoder(w).Encode(map[string]any{plural: out, "next_cursor": next}))
	}
	b := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { serveItems("b", w, r) }))
	t.Cleanup(b.Close)
	var a *httptest.Server
	a = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if writeSearchNotConfigured(w, r) {
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/regions" {
			type region struct {
				ID  string `json:"id"`
				URL string `json:"api_base_url"`
			}
			catalog := []region{{"region-a", a.URL + "/Api///"}, {"region-b", strings.Replace(a.URL, "http:", "HTTP:", 1) + "/Api/"}, {"region-c", a.URL + "/Api"}, {"region-d", b.URL}}
			f.mu.Lock()
			if !f.firstAliasVisible {
				catalog = catalog[1:]
			}
			f.mu.Unlock()
			require.NoError(t, json.NewEncoder(w).Encode(struct {
				Regions []region `json:"regions"`
			}{catalog}))
			return
		}
		serveItems("a", w, r)
	}))
	t.Cleanup(a.Close)
	f.home = a.URL
	return f
}

func (f *aliasListFixture) execute(t *testing.T, args ...string) (aliasListResult, error) {
	t.Helper()
	stdout := &bytes.Buffer{}
	root := NewRoot(stdout, &bytes.Buffer{})
	root.SetArgs(append([]string{"--api-url", f.home, "--api-key", "k", "-o", "json"}, args...))
	ctx, cancel := context.WithTimeout(t.Context(), 3*time.Second)
	defer cancel()
	err := root.ExecuteContext(ctx)
	var out aliasListResult
	if stdout.Len() > 0 {
		require.NoError(t, json.Unmarshal(stdout.Bytes(), &out))
	}
	return out, err
}

func TestListsRetainUnseenItemsWhenResumingAliasCursors(t *testing.T) {
	for _, kind := range []string{"sandbox", "image"} {
		for _, tc := range []struct {
			cursor string
			want   []string
		}{
			// Completed aliases and pending streams may share an API endpoint.
			{"region-a:=,region-b:,region-c:,region-d:5", []string{"a", "shared", "c", "d"}},
			// Opaque pending streams must not be collapsed to either token.
			{"region-a:=,region-b:6,region-c:4,region-d:5", []string{"shared", "c", "d"}},
			{"region-a:2,region-b:6,region-c:=,region-d:=", []string{"shared", "c"}},
			{"region-a:=,region-b:=,region-c:=,region-d:5", []string{"shared", "d"}},
		} {
			for _, limit := range []string{"1", "4"} {
				t.Run(kind+"/"+tc.cursor+"/"+limit, func(t *testing.T) {
					// Given stable API endpoint contents and an alias cursor.
					f := aliasListServer(t, false)
					// When following its merged continuation.
					out, err := f.execute(t, kind, "list", "--all", "--limit", limit, "--cursor", tc.cursor)
					// Then every unseen item survives exactly once.
					require.NoError(t, err)
					ids := []string{}
					for _, item := range out.Items {
						ids = append(ids, item.ID)
					}
					require.Equal(t, tc.want, ids)
					require.Empty(t, out.Next)
					if limit == "4" && tc.cursor == "region-a:=,region-b:6,region-c:4,region-d:5" {
						require.ElementsMatch(t, []string{"cursor=6&limit=4", "cursor=4&limit=4"}, f.requests["a"])
					}
					if tc.cursor == "region-a:=,region-b:=,region-c:=,region-d:5" {
						require.Empty(t, f.requests["a"])
					}
				})
			}
		}
	}
}

func TestListsPreserveAliasStreamsWhenRegionNarrowsSharedEndpoint(t *testing.T) {
	for _, kind := range []string{"sandbox", "image"} {
		t.Run(kind, func(t *testing.T) {
			// Given a done selected alias with two pending sibling streams.
			f := aliasListServer(t, false)
			// When selecting that alias's API endpoint, not the other API URL.
			out, err := f.execute(t, kind, "list", "--all", "--limit", "1", "--region", "REGION-A", "--cursor", "region-a:=,region-b:6,region-c:4,region-d:5")
			// Then the sibling streams are read without querying the other
			// endpoint, and their items, which are in other regions, are left
			// out.
			require.NoError(t, err)
			require.Empty(t, out.Items)
			require.Empty(t, out.Next)
			require.Empty(t, f.requests["b"])
			require.NotEmpty(t, f.requests["a"])
			require.Empty(t, f.requests["b"])
		})
	}
}

func TestListsKeepPartialExitWhenAliasEndpointFails(t *testing.T) {
	for _, kind := range []string{"sandbox", "image"} {
		t.Run(kind, func(t *testing.T) {
			// Given pending aliases and a failing second API endpoint.
			f := aliasListServer(t, true)
			// When following the working streams.
			out, err := f.execute(t, kind, "list", "--all", "--limit", "1", "--cursor", "region-a:=,region-b:6,region-c:4,region-d:5")
			// Then return successful items with incomplete-result exit 7.
			require.Equal(t, cerr.ExitPartial, cerr.ExitCode(err))
			require.True(t, out.Incomplete)
			require.Len(t, out.Items, 2)
			require.Equal(t, "shared", out.Items[0].ID)
			require.Equal(t, "c", out.Items[1].ID)
			require.Equal(t, "5", parseSandboxCursor(out.Next)["region-d"].cursor)
		})
	}
}

func TestListsResumeFailedAliasStream_whenEndpointRecovers(t *testing.T) {
	for _, kind := range []string{"sandbox", "image"} {
		t.Run(kind, func(t *testing.T) {
			// Given a saved partial result after the other streams completed.
			failed := aliasListServer(t, true)
			partial, err := failed.execute(t, kind, "list", "--all", "--limit", "1", "--cursor", "region-a:=,region-b:6,region-c:4,region-d:5")
			require.Equal(t, cerr.ExitPartial, cerr.ExitCode(err))
			require.NotEmpty(t, partial.Next)
			recovered := aliasListServer(t, false)

			// When the caller resumes that exact continuation after recovery.
			out, err := recovered.execute(t, kind, "list", "--all", "--limit", "1", "--cursor", partial.Next)

			// Then only the previously failed stream is fetched, without gaps.
			require.NoError(t, err)
			require.Len(t, out.Items, 2)
			require.Equal(t, "shared", out.Items[0].ID)
			require.Equal(t, "d", out.Items[1].ID)
			require.Empty(t, recovered.requests["a"])
			require.Empty(t, out.Next)
		})
	}
}

func TestListsResumeEndpoint_whenCatalogAddsEarlierAlias(t *testing.T) {
	for _, kind := range []string{"sandbox", "image"} {
		for _, region := range []string{"", "REGION-B"} {
			t.Run(kind+"/"+region, func(t *testing.T) {
				// Given a first page saved before region-a was added ahead of region-b.
				f := aliasListServer(t, false)
				f.mu.Lock()
				f.firstAliasVisible = false
				f.mu.Unlock()
				first, err := f.execute(t, kind, "list", "--limit", "1")
				require.NoError(t, err)
				require.Len(t, first.Items, 1)
				require.Equal(t, "a", first.Items[0].ID)
				require.Equal(t, "6", parseSandboxCursor(first.Next)["region-b"].cursor)
				f.mu.Lock()
				f.firstAliasVisible = true
				f.mu.Unlock()

				// When resuming against the updated catalog at the same API URL.
				args := []string{kind, "list", "--all", "--limit", "1", "--cursor", first.Next}
				if region != "" {
					args = append(args, "--region", region)
				}
				out, err := f.execute(t, args...)

				// Then no consumed item replays, and every remaining item survives.
				require.NoError(t, err)
				ids := []string{}
				for _, item := range out.Items {
					ids = append(ids, item.ID)
				}
				want := []string{"b", "shared", "c", "d"}
				if region != "" {
					want = []string{"shared"} // region-b's only remaining item
				}
				require.Equal(t, want, ids)
				require.Empty(t, out.Next)
			})
		}
	}
}
