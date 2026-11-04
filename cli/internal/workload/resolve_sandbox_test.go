// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package workload

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	api "github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestResolveSandbox_fallsBackAcrossRegionsConcurrently(t *testing.T) {
	startedSecond := make(chan struct{})
	regionA := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "/sandboxes/11111111-1111-1111-1111-111111111111", r.URL.Path)
		select {
		case <-startedSecond:
		case <-r.Context().Done():
			t.Fatalf("region A request was not concurrent: %v", r.Context().Err())
		}
		_, _ = w.Write([]byte(`{"class":"linux","spot":false,"cpu":1,"id":"11111111-1111-1111-1111-111111111111","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"memory_mib":512,"name":"box","port_map":[],"region_id":"region-a","disk_mib":1024,"status":"running","toolbox_proxy_url":"https://tb.example","cores_max":1}`))
	}))
	defer regionA.Close()
	regionB := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "/sandboxes/11111111-1111-1111-1111-111111111111", r.URL.Path)
		close(startedSecond)
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"code":"NOT_FOUND","message":"not found","source":"DAYTONA_API"}`))
	}))
	defer regionB.Close()
	home := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/11111111-1111-1111-1111-111111111111":
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"code":"NOT_FOUND","message":"not found","source":"DAYTONA_API"}`))
		case "/regions":
			_, _ = w.Write([]byte(`{"regions":[
				{"id":"region-a","name":"Region A","available":true,"api_base_url":"` + regionA.URL + `"},
				{"id":"region-b","name":"Region B","available":true,"api_base_url":"` + regionB.URL + `"}
			],"default_region_id":null}`))
		default:
			// Handler goroutine: report, answer, never FailNow.
			t.Errorf("unexpected path %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer home.Close()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	resolved, err := ResolveSandbox(ctx, api.New(home.URL, "k", "test"), NewCatalogCache(), "11111111-1111-1111-1111-111111111111")
	require.NoError(t, err)
	require.Equal(t, regionA.URL, resolved.Client.BaseURL)
}

func TestResolveSandbox_ambiguousAcrossRegions(t *testing.T) {
	var hits atomic.Int32
	regionHandler := func(region string) http.HandlerFunc {
		return func(w http.ResponseWriter, r *http.Request) {
			hits.Add(1)
			require.Equal(t, "/sandboxes/11111111-1111-1111-1111-111111111111", r.URL.Path)
			_, _ = w.Write([]byte(`{"class":"linux","spot":false,"cpu":1,"id":"11111111-1111-1111-1111-111111111111","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"memory_mib":512,"name":"box","port_map":[],"region_id":"` + region + `","disk_mib":1024,"status":"running","toolbox_proxy_url":"https://tb.example","cores_max":1}`))
		}
	}
	regionA := httptest.NewServer(regionHandler("region-a"))
	defer regionA.Close()
	regionB := httptest.NewServer(regionHandler("region-b"))
	defer regionB.Close()
	home := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/11111111-1111-1111-1111-111111111111":
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"code":"NOT_FOUND","message":"not found","source":"DAYTONA_API"}`))
		case "/regions":
			_, _ = w.Write([]byte(`{"regions":[
				{"id":"region-a","name":"Region A","available":true,"api_base_url":"` + regionA.URL + `"},
				{"id":"region-b","name":"Region B","available":true,"api_base_url":"` + regionB.URL + `"}
			],"default_region_id":null}`))
		default:
			// Handler goroutine: report, answer, never FailNow.
			t.Errorf("unexpected path %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer home.Close()
	_, err := ResolveSandbox(context.Background(), api.New(home.URL, "k", "test"), NewCatalogCache(), "11111111-1111-1111-1111-111111111111")
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.ErrorContains(t, err, "ambiguous across regions")
	require.GreaterOrEqual(t, hits.Load(), int32(2))
}

func TestResolveSandbox_probesRegionsWhenHomeReturnsNotFound(t *testing.T) {
	region := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "/sandboxes/11111111-1111-1111-1111-111111111111", r.URL.Path)
		_, _ = w.Write([]byte(`{"class":"linux","spot":false,"cpu":1,"id":"11111111-1111-1111-1111-111111111111","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"memory_mib":512,"name":"box","port_map":[],"region_id":"region-a","disk_mib":1024,"status":"running","toolbox_proxy_url":"https://tb.example","cores_max":1}`))
	}))
	defer region.Close()
	home := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/11111111-1111-1111-1111-111111111111":
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"code":"NOT_FOUND","message":"sandbox not found","source":"DAYTONA_API"}`))
		case "/regions":
			_, _ = w.Write([]byte(`{"regions":[
				{"id":"region-a","name":"Region A","available":true,"api_base_url":"` + region.URL + `"}
			],"default_region_id":null}`))
		default:
			// Handler goroutine: report, answer, never FailNow.
			t.Errorf("unexpected path %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer home.Close()

	resolved, err := ResolveSandbox(context.Background(), api.New(home.URL, "k", "test"), NewCatalogCache(), "11111111-1111-1111-1111-111111111111")
	require.NoError(t, err)
	require.Equal(t, region.URL, resolved.Client.BaseURL)
	require.Equal(t, "region-a", resolved.Detail.RegionId)
}

func TestResolveSandbox_returnsNotFoundWhenCatalogEmpty(t *testing.T) {
	home := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/11111111-1111-1111-1111-111111111111":
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"code":"NOT_FOUND","message":"sandbox not found","source":"DAYTONA_API"}`))
		case "/regions":
			_, _ = w.Write([]byte(`{"regions":[],"default_region_id":null}`))
		default:
			// Handler goroutine: report, answer, never FailNow.
			t.Errorf("unexpected path %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer home.Close()

	_, err := ResolveSandbox(context.Background(), api.New(home.URL, "k", "test"), NewCatalogCache(), "11111111-1111-1111-1111-111111111111")
	require.Error(t, err)
	var apiErr *api.APIError
	require.ErrorAs(t, err, &apiErr)
	require.Equal(t, "NOT_FOUND", apiErr.Code)
}
