// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"net/url"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func writeCodedError(t *testing.T, w http.ResponseWriter, status int, code string) {
	t.Helper()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	writeJSON(t, w, map[string]string{"code": code, "source": "API"})
}

func TestSecretsSearch_whenEveryFilterIsSet(t *testing.T) {
	// Given a search route answering one page with a count.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /secrets/search": jsonHandler(t, map[string]any{
			"secrets": []any{secretFixture()}, "next_cursor": "next", "total": 7, "total_relation": "gte",
		}),
	})
	client := newTestClient(t, fake.Server)
	start := time.Date(2026, 1, 1, 1, 0, 0, 0, time.FixedZone("CET", 3600))
	// When searching with every filter.
	page, err := client.Secrets().Search(t.Context(), &SearchSecretsOptions{
		Q: "tok", Name: "TOKEN", NamePrefix: "TO", AllowedDomain: "api.example.com",
		CreatedStartAt: start, CreatedEndAt: start.Add(time.Hour), UpdatedStartAt: start, UpdatedEndAt: start.Add(2 * time.Hour),
		Sort: SecretSearchSortUpdatedAt, Order: SortOrderDescending, Limit: 5, Cursor: "c1",
	})
	// Then the query carries each filter and the count is parsed.
	require.NoError(t, err)
	require.Equal(t, "TOKEN", page.Items[0].Name)
	require.Equal(t, "next", page.NextCursor)
	require.EqualValues(t, 7, page.Total)
	require.Equal(t, TotalRelationGreaterOrEqual, page.TotalRelation)
	require.Equal(t, url.Values{
		"q": {"tok"}, "name": {"TOKEN"}, "name_prefix": {"TO"}, "allowed_domain": {"api.example.com"},
		"created_start_at": {"2026-01-01T00:00:00Z"}, "created_end_at": {"2026-01-01T01:00:00Z"},
		"updated_start_at": {"2026-01-01T00:00:00Z"}, "updated_end_at": {"2026-01-01T02:00:00Z"},
		"sort": {"updated_at"}, "order": {"desc"}, "limit": {"5"}, "cursor": {"c1"},
	}, fake.requests()[0].Query)
}

func TestVolumesSearch_whenEveryFilterIsSet(t *testing.T) {
	// Given a search route answering one page with an unknown relation.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /volumes/search": jsonHandler(t, map[string]any{
			"volumes": []any{volumeFixture()}, "total": 1, "total_relation": "future",
		}),
	})
	client := newTestClient(t, fake.Server)
	imported := false
	at := time.Date(2026, 2, 3, 4, 5, 6, 0, time.UTC)
	// When searching with every filter.
	page, err := client.Volumes().Search(t.Context(), &SearchVolumesOptions{
		Q: "data", Name: "data", NamePrefix: "da",
		Types:    []VolumeType{VolumeTypeDisk, VolumeTypeBucket},
		Statuses: []VolumeStatus{VolumeStatusReady, VolumeStatusError},
		Imported: &imported, CreatedStartAt: at, CreatedEndAt: at, UpdatedStartAt: at, UpdatedEndAt: at,
		LastUsedStartAt: at, LastUsedEndAt: at, Sort: VolumeSearchSortSizeGiB, Order: SortOrderAscending, Limit: 3,
	})
	// Then the lists are comma-joined and the relation is preserved verbatim.
	require.NoError(t, err)
	require.Len(t, page.Items, 1)
	require.Empty(t, page.NextCursor)
	require.Equal(t, TotalRelationUnknown, page.TotalRelation)
	require.Equal(t, "future", page.RawTotalRelation)
	stamp := "2026-02-03T04:05:06Z"
	require.Equal(t, url.Values{
		"q": {"data"}, "name": {"data"}, "name_prefix": {"da"}, "type": {"disk,bucket"}, "status": {"ready,error"},
		"imported": {"false"}, "created_start_at": {stamp}, "created_end_at": {stamp}, "updated_start_at": {stamp},
		"updated_end_at": {stamp}, "last_used_start_at": {stamp}, "last_used_end_at": {stamp},
		"sort": {"size_gib"}, "order": {"asc"}, "limit": {"3"},
	}, fake.requests()[0].Query)
}

func TestResourceSearch_whenOptionsAreOmittedTheQueryIsEmpty(t *testing.T) {
	// Given search routes for both resources.
	page := func(key string) http.HandlerFunc {
		return jsonHandler(t, map[string]any{key: []any{}, "total": 0, "total_relation": "eq"})
	}
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /secrets/search": page("secrets"), "GET /volumes/search": page("volumes"),
	})
	client := newTestClient(t, fake.Server)
	// When searching with nil options.
	secrets, secretErr := client.Secrets().Search(t.Context(), nil)
	volumes, volumeErr := client.Volumes().Search(t.Context(), nil)
	// Then no filter is sent and the exact count is reported.
	require.NoError(t, secretErr)
	require.NoError(t, volumeErr)
	require.Equal(t, TotalRelationEqual, secrets.TotalRelation)
	require.Equal(t, TotalRelationEqual, volumes.TotalRelation)
	for _, request := range fake.requests() {
		require.Empty(t, request.Query, request.Path)
	}
}

func TestResourceSearch_whenOptionsAreInvalid(t *testing.T) {
	for name, search := range map[string]func(*Client) error{
		"secret sort": func(c *Client) error {
			_, e := c.Secrets().Search(t.Context(), &SearchSecretsOptions{Sort: "size_gib"})
			return e
		},
		"secret order": func(c *Client) error {
			_, e := c.Secrets().Search(t.Context(), &SearchSecretsOptions{Order: "up"})
			return e
		},
		"secret text": func(c *Client) error {
			_, e := c.Secrets().Search(t.Context(), &SearchSecretsOptions{Q: "\xff"})
			return e
		},
		"secret limit": func(c *Client) error {
			_, e := c.Secrets().Search(t.Context(), &SearchSecretsOptions{Limit: 201})
			return e
		},
		"volume types": func(c *Client) error {
			_, e := c.Volumes().Search(t.Context(), &SearchVolumesOptions{Types: []VolumeType{}})
			return e
		},
		"volume type": func(c *Client) error {
			_, e := c.Volumes().Search(t.Context(), &SearchVolumesOptions{Types: []VolumeType{VolumeTypeUnknown}})
			return e
		},
		"volume status": func(c *Client) error {
			_, e := c.Volumes().Search(t.Context(), &SearchVolumesOptions{Statuses: []VolumeStatus{VolumeStatusDeleted}})
			return e
		},
		"volume empty": func(c *Client) error {
			_, e := c.Volumes().Search(t.Context(), &SearchVolumesOptions{Statuses: []VolumeStatus{}})
			return e
		},
		"volume sort": func(c *Client) error {
			_, e := c.Volumes().Search(t.Context(), &SearchVolumesOptions{Sort: "cores"})
			return e
		},
		"volume year": func(c *Client) error {
			_, e := c.Volumes().Search(t.Context(), &SearchVolumesOptions{LastUsedEndAt: time.Date(10000, 1, 1, 0, 0, 0, 0, time.UTC)})
			return e
		},
	} {
		t.Run(name, func(t *testing.T) {
			// Given a server that must not be called.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When searching with an invalid option.
			err := search(client)
			// Then validation is local.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestResourceSearch_whenSearchIsNotServedItNeverFallsBack(t *testing.T) {
	// Given a deployment without search.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /volumes/search": func(w http.ResponseWriter, _ *http.Request) {
			writeCodedError(t, w, 501, "SEARCH_NOT_CONFIGURED")
		},
	})
	client := newTestClient(t, fake.Server)
	// When searching explicitly.
	_, err := client.Volumes().Search(t.Context(), nil)
	// Then the condition is reported, not hidden behind the list route.
	require.ErrorIs(t, err, ErrSearchNotConfigured)
	require.Len(t, fake.requests(), 1)
}

func TestResourceLists_whenSearchIsNotServedTheyFallBack(t *testing.T) {
	for name, answer := range map[string]func(*testing.T, http.ResponseWriter, string){
		"501": func(t *testing.T, w http.ResponseWriter, _ string) {
			writeCodedError(t, w, 501, "SEARCH_NOT_CONFIGURED")
		},
		"404": func(_ *testing.T, w http.ResponseWriter, _ string) { w.WriteHeader(404) },
		"item": func(t *testing.T, w http.ResponseWriter, resource string) {
			// An older API answers /<kind>/search with the resource named "search".
			if resource == "secrets" {
				writeJSON(t, w, secretFixture())
				return
			}
			writeJSON(t, w, volumeFixture())
		},
	} {
		for _, resource := range []string{"secrets", "volumes"} {
			t.Run(name+"_"+resource, func(t *testing.T) {
				// Given a deployment that does not serve this resource's search.
				var searches, lists atomic.Int32
				var fixture any = volumeFixture()
				if resource == "secrets" {
					fixture = secretFixture()
				}
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"GET /" + resource + "/search": func(w http.ResponseWriter, _ *http.Request) {
						searches.Add(1)
						answer(t, w, resource)
					},
					"GET /" + resource: func(w http.ResponseWriter, _ *http.Request) {
						lists.Add(1)
						writeJSON(t, w, map[string]any{resource: []any{fixture}})
					},
				})
				client := newTestClient(t, fake.Server)
				// When listing twice.
				for range 2 {
					count, err := listCount(t.Context(), client, resource)
					require.NoError(t, err)
					require.Equal(t, 1, count)
				}
				// Then the list route serves both and the fallback is remembered.
				require.EqualValues(t, 1, searches.Load())
				require.EqualValues(t, 2, lists.Load())
			})
		}
	}
}

func TestResourceLists_whenSearchIsUnavailableTheErrorIsKept(t *testing.T) {
	// Given configured search that is down.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /secrets/search": func(w http.ResponseWriter, _ *http.Request) {
			writeCodedError(t, w, 503, "SEARCH_UNAVAILABLE")
		},
	})
	client := newTestClient(t, fake.Server)
	// When listing.
	_, err := client.Secrets().List(t.Context(), nil)
	// Then the outage is reported and the list route is not read.
	require.ErrorIs(t, err, ErrSearchUnavailable)
	require.Len(t, fake.requests(), 1)
}

func TestResourceLists_whenRememberedFallbackIsRetiredSearchIsRetried(t *testing.T) {
	// Given a fallback learned before the list route was retired.
	var searches, lists atomic.Int32
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /volumes/search": func(w http.ResponseWriter, _ *http.Request) {
			if searches.Add(1) == 1 {
				writeCodedError(t, w, 501, "SEARCH_NOT_CONFIGURED")
				return
			}
			writeJSON(t, w, map[string]any{"volumes": []any{volumeFixture()}, "total": 1, "total_relation": "eq"})
		},
		"GET /volumes": func(w http.ResponseWriter, _ *http.Request) {
			if lists.Add(1) == 2 {
				writeCodedError(t, w, 410, "GONE")
				return
			}
			writeJSON(t, w, map[string]any{"volumes": []any{volumeFixture()}})
		},
	})
	client := newTestClient(t, fake.Server)
	// When listing three times across the switch.
	for range 3 {
		count, err := listCount(t.Context(), client, "volumes")
		require.NoError(t, err)
		require.Equal(t, 1, count)
	}
	// Then the 410 sends the client back to search, which serves the third call too.
	require.EqualValues(t, 3, searches.Load())
	require.EqualValues(t, 2, lists.Load())
}

func listCount(ctx context.Context, client *Client, resource string) (int, error) {
	if resource == "secrets" {
		page, err := client.Secrets().List(ctx, nil)
		if err != nil {
			return 0, err
		}
		return len(page.Items), nil
	}
	page, err := client.Volumes().List(ctx, nil)
	if err != nil {
		return 0, err
	}
	return len(page.Items), nil
}

func TestResourceNames_whenNameIsSearchItIsRefusedLocally(t *testing.T) {
	ctx := t.Context()
	domains := &SetSecretOptions{AllowedDomains: []string{"*"}}
	for name, call := range map[string]func(*Client) error{
		"secret get":    func(c *Client) error { _, e := c.Secrets().Get(ctx, "search"); return e },
		"secret set":    func(c *Client) error { _, e := c.Secrets().Set(ctx, "search", "v", domains); return e },
		"secret delete": func(c *Client) error { return c.Secrets().Delete(ctx, "search") },
		"volume get": func(c *Client) error {
			_, e := c.Volumes().Get(ctx, "search", &GetVolumeOptions{Create: true})
			return e
		},
		"volume create": func(c *Client) error { _, e := c.Volumes().Create(ctx, "search", nil); return e },
		"volume delete": func(c *Client) error { return c.Volumes().Delete(ctx, "search", nil) },
	} {
		t.Run(name, func(t *testing.T) {
			// Given a server that must not be called.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When addressing a secret or volume named "search".
			err := call(client)
			// Then the call is refused before any request.
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, "reserved for the search route")
			require.Empty(t, fake.requests())
		})
	}
}

func TestResourceNames_whenNameDiffersInCaseItIsSent(t *testing.T) {
	// Given a secret named SEARCH.
	fixture := secretFixture()
	fixture["name"] = "SEARCH"
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /secrets/SEARCH": jsonHandler(t, fixture)})
	client := newTestClient(t, fake.Server)
	// When reading it.
	secret, err := client.Secrets().Get(t.Context(), "SEARCH")
	// Then only the exact lowercase name is reserved.
	require.NoError(t, err)
	require.Equal(t, "SEARCH", secret.Name)
}
