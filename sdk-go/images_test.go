// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"net/url"
	"sync/atomic"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestImagesList_whenFiltersAreSet(t *testing.T) {
	// Given a filtered page with another page available.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /images/search": jsonHandler(t, map[string]any{
			"images": []any{imageFixture("img", "ready")}, "next_cursor": "next",
		}),
	})
	client := newTestClient(t, fake.Server)
	anonymous := false
	// When reading one page.
	page, err := client.Images().List(t.Context(), &ListImagesOptions{
		Cursor: "first/+", Limit: 2, Name: "base", Anonymous: &anonymous, SandboxID: "sbx", Platform: ImagePlatformAny,
	})
	// Then every filter is preserved and the next page is not fetched.
	require.NoError(t, err)
	require.Len(t, page.Items, 1)
	require.Equal(t, "img", page.Items[0].ID)
	require.Equal(t, "next", page.NextCursor)
	requests := fake.requests()
	require.Len(t, requests, 1)
	require.Equal(t, url.Values{
		"cursor": {"first/+"}, "limit": {"2"}, "name": {"base"}, "anonymous": {"false"},
		"sandbox_id": {"sbx"}, "platform": {"any"},
	}, requests[0].Query)
}

func TestImagesList_whenFallbackIsRememberedAndRecovers(t *testing.T) {
	for _, status := range []int{404, 501} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			// Given unavailable search followed by retirement of the legacy route.
			var searches, lists atomic.Int32
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /images/search": func(w http.ResponseWriter, _ *http.Request) {
					if searches.Add(1) == 1 {
						w.Header().Set("Content-Type", "application/json")
						w.WriteHeader(status)
						writeJSON(t, w, map[string]string{"code": "SEARCH_NOT_CONFIGURED", "source": "API"})
						return
					}
					writeJSON(t, w, map[string]any{"images": []any{imageFixture("search", "ready")}})
				},
				"GET /images": func(w http.ResponseWriter, _ *http.Request) {
					if lists.Add(1) == 3 {
						w.Header().Set("Content-Type", "application/json")
						w.WriteHeader(410)
						writeJSON(t, w, map[string]string{"code": "GONE", "source": "API"})
						return
					}
					writeJSON(t, w, map[string]any{"images": []any{imageFixture("legacy", "ready")}})
				},
			})
			client := newTestClient(t, fake.Server)
			// When each call obtains a fresh service handle.
			ids := make([]string, 0, 4)
			for range 4 {
				page, err := client.Images().List(t.Context(), nil)
				require.NoError(t, err)
				require.Len(t, page.Items, 1)
				ids = append(ids, page.Items[0].ID)
			}
			// Then fallback is client-owned and a GONE response restores search.
			require.Equal(t, []string{"legacy", "legacy", "search", "search"}, ids)
			require.EqualValues(t, 3, searches.Load())
			require.EqualValues(t, 3, lists.Load())
		})
	}
}

func TestImagesList_whenSearchFailsWithoutFallbackSignal(t *testing.T) {
	for _, tt := range []struct {
		status int
		code   string
	}{
		{403, "FORBIDDEN"},
		{500, "INTERNAL_SERVER_ERROR"},
		{501, "FUTURE"},
		{400, "SEARCH_NOT_CONFIGURED"},
	} {
		t.Run(tt.code+http.StatusText(tt.status), func(t *testing.T) {
			// Given a search failure that does not mean the endpoint is unavailable.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /images/search": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(tt.status)
					writeJSON(t, w, map[string]string{"code": tt.code, "source": "API"})
				},
			})
			client := newTestClient(t, fake.Server)
			// When listing.
			_, err := client.Images().List(t.Context(), nil)
			// Then the original response survives and no legacy call occurs.
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, tt.status, detail.StatusCode)
			require.Equal(t, tt.code, detail.Code)
			require.Equal(t, "API", detail.Source)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestImagesListAll_whenCursorCycles(t *testing.T) {
	// Given a non-adjacent cycle back to the initial cursor.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /images/search": func(w http.ResponseWriter, r *http.Request) {
			cursor := r.URL.Query().Get("cursor")
			next := map[string]string{"first": "second", "second": "third", "third": "first"}[cursor]
			writeJSON(t, w, map[string]any{"images": []any{imageFixture(cursor, "ready")}, "next_cursor": next})
		},
	})
	client := newTestClient(t, fake.Server)
	// When explicitly traversing every page.
	var ids []string
	for image, err := range client.Images().ListAll(t.Context(), &ListImagesOptions{Cursor: "first"}) {
		require.NoError(t, err)
		ids = append(ids, image.ID)
	}
	// Then each cursor is visited exactly once.
	require.Equal(t, []string{"first", "second", "third"}, ids)
	require.Len(t, fake.requests(), 3)
}

func TestImagesListAll_whenOptionsChangeAndConsumerBreaks(t *testing.T) {
	// Given an iterator whose caller later edits both the options and pointed flag.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /images/search": jsonHandler(t, map[string]any{
			"images": []any{imageFixture("img", "ready")}, "next_cursor": "next",
		}),
	})
	client := newTestClient(t, fake.Server)
	anonymous := false
	options := ListImagesOptions{Name: "original", Anonymous: &anonymous}
	images := client.Images().ListAll(t.Context(), &options)
	options.Name, anonymous = "changed", true
	require.Empty(t, fake.requests())
	// When the consumer stops after its first item.
	for image, err := range images {
		require.NoError(t, err)
		require.Equal(t, "img", image.ID)
		break
	}
	// Then iteration uses call-time filters and performs no prefetch.
	requests := fake.requests()
	require.Len(t, requests, 1)
	require.Equal(t, "original", requests[0].Query.Get("name"))
	require.Equal(t, "false", requests[0].Query.Get("anonymous"))
}

func TestImagesList_whenPageIsMalformed(t *testing.T) {
	for _, tt := range []struct {
		name string
		body any
	}{
		{"null", nil},
		{"array", []any{}},
		{"missing images", map[string]any{}},
		{"null images", map[string]any{"images": nil}},
		{"object images", map[string]any{"images": map[string]any{}}},
		{"null element", map[string]any{"images": []any{nil}}},
		{"scalar element", map[string]any{"images": []any{"image"}}},
		{"cursor number", map[string]any{"images": []any{}, "next_cursor": 1}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given a page that violates the consumed response shape.
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /images/search": jsonHandler(t, tt.body)})
			client := newTestClient(t, fake.Server)
			// When reading one page.
			_, err := client.Images().List(t.Context(), nil)
			// Then invalid envelopes and items are rejected.
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}
