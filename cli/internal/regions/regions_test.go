// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package regions

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestTagOf(t *testing.T) {
	cases := map[string]string{
		"7fJk2mQpXv9dLc3RtY-regionc":           "regionc",
		"dff9d7a8-8afd-4b06-8622-12913af6a130": "",
		"7fJk2mQpXv9dLc3RtY":                   "",
		"abc-":                                 "",
	}
	for in, want := range cases {
		if got := TagOf(in); got != want {
			t.Errorf("%q: got %q want %q", in, got, want)
		}
	}
}

func TestTagOf_prefersLongestCatalogSuffix(t *testing.T) {
	cat := &Catalog{Regions: []gen.Region{{Id: "b", ApiBaseUrl: str("https://one.example")}, {Id: "parent-region-b", ApiBaseUrl: str("https://other.example")}}}
	require.Equal(t, "parent-region-b", TagOf("abc-parent-region-b", cat))
	require.Equal(t, "", TagOf("abc-unknown", cat))
}

func TestTagOf_prefersLongestSuffixOverAShorterURLBearingRegion(t *testing.T) {
	// A sandbox id carries the whole region id, so the longest listed id that
	// ends it is the tag — whether or not that region publishes an API URL. The
	// home region publishes none, and a remote `region-b` must not claim
	// `…-parent-region-b`.
	cat := &Catalog{Regions: []gen.Region{
		{Id: "region-root"},
		{Id: "region-b", ApiBaseUrl: str("https://other.example")},
		{Id: "parent-region-b"},
	}}
	require.Equal(t, "parent-region-b", TagOf("abc-parent-region-b", cat))
	require.Equal(t, "region-b", TagOf("abc-region-b", cat))
}

func TestCatalogFind_matchesEveryListedRegion(t *testing.T) {
	home := "https://home"
	cat := &Catalog{Regions: []gen.Region{
		{Id: "region-root"},
		{Id: "group-z"},
		{Id: "group-z-sub"},
		{Id: "parent-region-b", Available: true},
		{Id: "region-a-1", ApiBaseUrl: str("https://other.example/"), Available: true},
	}}
	require.NotNil(t, cat.Find("PARENT-REGION-B"))
	require.Equal(t, home, BaseFor(*cat.Find("parent-region-b"), home), "a region without a URL uses the client base URL")
	require.Equal(t, "https://other.example", BaseFor(*cat.Find("region-a-1"), home))
	require.Equal(t, home, BaseFor(*cat.Find("region-root"), home))
	require.Nil(t, cat.Find("region-missing"))
}

func TestListRegionKeys_oneEndpointPerAPINamedByItsMostSpecificRegion(t *testing.T) {
	cat := &Catalog{Regions: []gen.Region{
		{Id: "region-root"},
		{Id: "group-a", ParentRegionIds: []string{"region-root"}},
		{Id: "region-a-1", ApiBaseUrl: str("https://other.example"), Available: true, ParentRegionIds: []string{"region-root", "group-a"}},
		{Id: "region-a-2", ApiBaseUrl: str("https://other.example"), Available: true, ParentRegionIds: []string{"region-root", "group-a"}},
		{Id: "group-z", ParentRegionIds: []string{"region-root"}},
		{Id: "group-z-sub", ParentRegionIds: []string{"region-root", "group-z"}},
		{Id: "parent-region-b", Available: true, ParentRegionIds: []string{"region-root", "group-z", "group-z-sub"}},
	}}
	require.Equal(t, map[string]string{
		"region-root": "parent-region-b", "group-z": "parent-region-b", "group-z-sub": "parent-region-b", "parent-region-b": "parent-region-b", "group-a": "parent-region-b",
		"region-a-1": "region-a-1", "region-a-2": "region-a-1",
	}, cat.ListRegionKeys("https://home", ""),
		"parent regions use the default URL, labeled by its most specific region")
}

func TestOrderedRegions_ordersParentsByParentRegionIDsNotByIDPrefix(t *testing.T) {
	// `region-a` sits under `parent-b` although no ID is a prefix of
	// the other; `group-z-old` shares a prefix with `group-z` but is not inside it.
	cat := &Catalog{Regions: []gen.Region{
		{Id: "region-root"},
		{Id: "region-a", ParentRegionIds: []string{"region-root", "group-z", "parent-b"}},
		{Id: "parent-b", ParentRegionIds: []string{"region-root", "group-z"}},
		{Id: "group-z", ParentRegionIds: []string{"region-root"}},
		{Id: "group-z-old", ParentRegionIds: []string{"region-root", "parent-c"}},
		{Id: "parent-c", ParentRegionIds: []string{"region-root"}},
	}}
	var ids []string
	for _, r := range cat.OrderedRegions() {
		ids = append(ids, r.Id)
	}
	require.Equal(t, []string{"group-z-old", "region-a", "group-z", "parent-b", "parent-c", "region-root"}, ids)
}

func TestSplitCursor(t *testing.T) {
	m := SplitCursor("region-c:aaa,region-d:bbb")
	if m["region-c"] != "aaa" || m["region-d"] != "bbb" {
		t.Fatal(m)
	}
	if SplitCursor("plain")[""] != "plain" {
		t.Fatal()
	}
}

func str(s string) *string { return &s }

func TestFanOutPartial(t *testing.T) {
	good := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"sandboxes": []map[string]string{{"id": "a"}}, "next_cursor": "c1"})
	}))
	defer good.Close()
	bad := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(502)
		_, _ = w.Write([]byte(`{"code":"BAD_GATEWAY","message":"down","source":"DAYTONA_API"}`))
	}))
	defer bad.Close()
	homeServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"sandboxes": []map[string]string{}})
	}))
	defer homeServer.Close()
	cat := &Catalog{Regions: []gen.Region{
		{Id: "region-root"},
		{Id: "region-c", ApiBaseUrl: str(good.URL), ParentRegionIds: []string{"region-root"}},
		{Id: "region-d", ApiBaseUrl: str(bad.URL), ParentRegionIds: []string{"region-root"}},
		// Regions without a URL share one request to the client base URL.
		{Id: "group-z", ParentRegionIds: []string{"region-root"}},
		{Id: "parent-region-b", Available: true, ParentRegionIds: []string{"region-root", "group-z"}},
	}}
	home := api.New(homeServer.URL, "k", "t")
	type item struct{ ID string }
	l, _ := FanOut(context.Background(), home, cat, "", func(ctx context.Context, c *api.Client, region string) (Page[item], error) {
		var resp struct {
			Sandboxes  []item `json:"sandboxes"`
			NextCursor string `json:"next_cursor"`
		}
		if err := c.Do(ctx, "GET", "/sandboxes", nil, nil, &resp); err != nil {
			return Page[item]{}, err
		}
		return Page[item]{Items: resp.Sandboxes, NextCursor: resp.NextCursor}, nil
	}, nil)
	if !l.Incomplete || len(l.Items) != 1 || len(l.Errors) != 1 || l.Errors[0].Region != "region-d" || l.Errors[0].Status != 502 {
		t.Fatalf("%+v", l)
	}
	// The default URL, labeled by parent-region-b, returned no continuation;
	// only region-c continues.
	if l.NextCursor != "region-c:c1" {
		t.Fatalf("cursor %q", l.NextCursor)
	}
	err := PartialError(l)
	if cerr.ExitCode(err) != cerr.ExitPartial || !strings.Contains(err.Error(), "region-d") {
		t.Fatal(err)
	}
	if d := cerr.DetailsOf(err); d.Source != "" || d.Code != "" {
		t.Fatalf("a partial list is not the CLI's own failure: %+v", d)
	}
	// An unknown --region is a usage error before any list request.
	called := false
	_, err = FanOut(context.Background(), home, cat, "nope", func(context.Context, *api.Client, string) (Page[item], error) {
		called = true
		return Page[item]{}, nil
	}, nil)
	if cerr.ExitCode(err) != cerr.ExitUsage || called {
		t.Fatalf("unknown region: %v (called=%v)", err, called)
	}
	// When no endpoint answers, the failure is the first endpoint's own.
	_, err = FanOut(context.Background(), home, cat, "region-d", func(ctx context.Context, c *api.Client, region string) (Page[item], error) {
		return Page[item]{}, c.Do(ctx, "GET", "/sandboxes", nil, nil, nil)
	}, nil)
	if cerr.ExitCode(err) != cerr.ExitFailure || !strings.Contains(err.Error(), "502") {
		t.Fatalf("all failed: %v", err)
	}
}

func TestClientFor(t *testing.T) {
	cat := &Catalog{Regions: []gen.Region{{Id: "region-c", ApiBaseUrl: str("https://region-c.example")}}}
	home := api.New("https://home", "k", "t")
	if c := ClientFor(context.Background(), home, cat, "abc-region-c"); c.BaseURL != "https://region-c.example" {
		t.Fatal(c.BaseURL)
	}
	if c := ClientFor(context.Background(), home, cat, "abc-zzz"); c.BaseURL != "https://home" {
		t.Fatal("unknown tag falls back to home")
	}
	if c := ClientFor(context.Background(), home, nil, "abc-region-c"); c.BaseURL != "https://home" {
		t.Fatal("nil catalog -> home")
	}
}
