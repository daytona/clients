// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestRegionalLists_multipleURLsKeepIndependentCursorsFromBareStart(t *testing.T) {
	for _, resource := range []string{"sandbox", "image"} {
		t.Run(resource, func(t *testing.T) {
			// Given three regions served by two API endpoints with interleaved pages.
			var first, resumed, otherCalls atomic.Int32
			regional := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Query().Get("cursor") == "c1" {
					resumed.Add(1)
					writeListItems(t, w, resource, []listTestItem{{"two", "north", "c2"}}, "")
					return
				}
				first.Add(1)
				writeListItems(t, w, resource, []listTestItem{{"one", "east", "c1"}, {"two", "north", "c2"}}, "")
			}))
			defer regional.Close()
			other := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				otherCalls.Add(1)
				if cursor := r.URL.Query().Get("cursor"); cursor != "" && cursor != "start" {
					t.Errorf("unexpected other cursor %q", r.URL.Query().Get("cursor"))
				}
				writeListItems(t, w, resource, []listTestItem{{"three", "west", "c3"}}, "")
			}))
			defer other.Close()
			catalog := []gen.Region{listTestRegion("east", regional.URL), listTestRegion("north", regional.URL+"/"), listTestRegion("west", other.URL)}
			// When
			out, err := executeRegionalList(t, resource, catalog, []string{"--all", "--limit", "1", "--cursor", "start"})
			// Then
			require.NoError(t, err)
			require.Len(t, out.Items, 3)
			require.Equal(t, []string{"one", "two", "three"}, []string{out.Items[0].ID, out.Items[1].ID, out.Items[2].ID})
			require.Equal(t, int32(1), first.Load())
			require.Equal(t, int32(1), resumed.Load())
			require.Equal(t, int32(3), otherCalls.Load())
			require.Empty(t, out.NextCursor)
		})
	}
}

func TestRegionalLists_regionSelectsOnlyItsEndpointAndPreservesCursor(t *testing.T) {
	for _, resource := range []string{"sandbox", "image"} {
		t.Run(resource, func(t *testing.T) {
			// Given a selected alias and an unrelated API endpoint that must not be queried.
			var calls atomic.Int32
			regional := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				if r.URL.Query().Get("cursor") != "start" {
					t.Errorf("cursor = %q", r.URL.Query().Get("cursor"))
				}
				writeListItems(t, w, resource, []listTestItem{{"one", "north", "c1"}, {"two", "east", "c1"}}, "c1")
			}))
			defer regional.Close()
			catalog := []gen.Region{listTestRegion("east", regional.URL), listTestRegion("north", regional.URL+"/"), listTestRegion("west", "http://127.0.0.1:1")}
			// When
			out, err := executeRegionalList(t, resource, catalog, []string{"--region", "NORTH", "--cursor", "north:start"})
			// Then
			require.NoError(t, err)
			require.Len(t, out.Items, 1)
			require.Equal(t, int32(1), calls.Load())
			require.Equal(t, "north:c1", out.NextCursor)
		})
	}
}

func TestRegionalLists_allDeduplicatesIDsAcrossPages(t *testing.T) {
	for _, resource := range []string{"sandbox", "image"} {
		t.Run(resource, func(t *testing.T) {
			// Given successive pages containing the same resource ID.
			var calls atomic.Int32
			regional := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				if r.URL.Query().Get("cursor") == "c1" {
					writeListItems(t, w, resource, []listTestItem{{"one", "east", "c2"}}, "")
					return
				}
				writeListItems(t, w, resource, []listTestItem{{"one", "east", "c1"}}, "c1")
			}))
			defer regional.Close()
			// When
			out, err := executeRegionalList(t, resource, []gen.Region{listTestRegion("east", regional.URL)}, []string{"--all"})
			// Then
			require.NoError(t, err)
			require.Len(t, out.Items, 1)
			require.Equal(t, int32(2), calls.Load())
		})
	}
}

func TestRegionalLists_advanceEverySource_whenEndpointsShareAnID(t *testing.T) {
	for _, resource := range []string{"sandbox", "image"} {
		t.Run(resource, func(t *testing.T) {
			// Given two independent API endpoints whose first pages contain the same ID.
			var resumed, first atomic.Int32
			newEndpoint := func(region string) *httptest.Server {
				return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.URL.Query().Get("cursor") == "c1" {
						resumed.Add(1)
						writeListItems(t, w, resource, []listTestItem{{region, region, "c2"}}, "")
						return
					}
					first.Add(1)
					writeListItems(t, w, resource, []listTestItem{{"shared", region, "c1"}}, "c1")
				}))
			}
			east, west := newEndpoint("east"), newEndpoint("west")
			defer east.Close()
			defer west.Close()
			catalog := []gen.Region{listTestRegion("east", east.URL), listTestRegion("west", west.URL)}

			// When following a saved cursor with a one-item global page budget.
			out, err := executeRegionalList(t, resource, catalog, []string{"--all", "--limit", "1", "--cursor", "east:start,west:start"})

			// Then both API endpoints advance past the duplicate and expose their tails.
			require.NoError(t, err)
			require.Len(t, out.Items, 3)
			require.Equal(t, "shared", out.Items[0].ID)
			require.ElementsMatch(t, []string{"east", "west"}, []string{out.Items[1].ID, out.Items[2].ID})
			require.GreaterOrEqual(t, resumed.Load(), int32(2))
			require.Equal(t, int32(2), first.Load(), "a source must not restart its consumed page")
			require.Empty(t, out.NextCursor)
		})
	}
}

func TestRegionalLists_keepFailedAliasCursor_whenSiblingIsDone(t *testing.T) {
	for _, resource := range []string{"sandbox", "image"} {
		t.Run(resource, func(t *testing.T) {
			// Given an alias still pending after its sibling completed.
			regional := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				require.Equal(t, "6", r.URL.Query().Get("cursor"))
				w.WriteHeader(http.StatusServiceUnavailable)
			}))
			defer regional.Close()
			catalog := []gen.Region{listTestRegion("east", regional.URL), listTestRegion("north", regional.URL)}

			// When the pending alias fails, with no working stream left to advance.
			out, err := executeRegionalList(t, resource, catalog, []string{"--all", "--cursor", "east:=,north:6"})

			// Then exit 7 retains that exact continuation, not an empty cursor.
			require.Equal(t, cerr.ExitPartial, cerr.ExitCode(err))
			require.Equal(t, "east:=,north:6", out.NextCursor)
			require.True(t, out.Incomplete)
		})
	}
}
