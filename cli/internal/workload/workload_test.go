// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package workload

import (
	"context"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"

	gen "github.com/daytona/clients/cli/gen/api"
	api "github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/input"
	"github.com/spf13/pflag"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestBodyHelpers_parseAndDecode(t *testing.T) {
	body := input.Body{}
	NestedBody(body, "lifecycle_policy").Set("timeout_seconds", int64(60))
	SetNestedIfChanged(body, flagSet(t, "on-timeout"), "on-timeout", "lifecycle_policy", "on_timeout", "delete")
	SetIfChanged(body, flagSet(t, "name"), "name", "name", "box")
	require.NoError(t, SetKVMapIfChanged(body, flagSet(t, "env"), "env", "env", "--env", []string{"A=B"}))
	SetSliceIfChanged(body, flagSet(t, "secret"), "secret", "secrets", []string{"db"})
	ports, err := ParsePorts([]string{"8080/tcp"})
	require.NoError(t, err)
	body.Set("ports", ports)
	req, err := DecodeBody[gen.CreateSandboxRequest](body)
	require.NoError(t, err)
	require.Equal(t, "box", *req.Name)
	require.Equal(t, "B", (*req.Env)["A"])
	require.Equal(t, int32(8080), (*req.Ports)[0].Port)
}

func TestParseHelpers_rejectBadInput(t *testing.T) {
	_, err := ParsePorts([]string{"x"})
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	_, err = DecodeBody[gen.CreateSandboxRequest](input.Body{"cpu": "bad"})
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestResolveSandbox_prefersTaggedRegion(t *testing.T) {
	regional := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "/sandboxes/sbx-region-a", r.URL.Path)
		_, _ = w.Write([]byte(`{"class":"linux","spot":false,"cpu":1,"id":"sbx-region-a","image":{"id":"img_123","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"memory_mib":512,"name":"box","port_map":[],"region_id":"region-a","disk_mib":1024,"status":"running","toolbox_proxy_url":"https://tb.example","cores_max":1}`))
	}))
	defer regional.Close()
	home := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "/regions", r.URL.Path)
		_, _ = w.Write([]byte(`{"regions":[{"id":"region-a","name":"Region A","available":true,"api_base_url":"` + regional.URL + `"}],"default_region_id":null}`))
	}))
	defer home.Close()
	resolved, err := ResolveSandbox(context.Background(), api.New(home.URL, "k", "test"), NewCatalogCache(), "sbx-region-a")
	require.NoError(t, err)
	require.Equal(t, regional.URL, resolved.Client.BaseURL)
	require.Equal(t, "region-a", resolved.Detail.RegionId)
}

func TestResolveSandbox_fallsBackAcrossRegions(t *testing.T) {
	hits := 0
	regional := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		assert.Equal(t, "/sandboxes/11111111-1111-1111-1111-111111111111", r.URL.Path)
		_, _ = w.Write([]byte(`{"class":"linux","spot":false,"cpu":1,"id":"11111111-1111-1111-1111-111111111111","image":{"id":"img_123","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"memory_mib":512,"name":"box","port_map":[],"region_id":"region-a","disk_mib":1024,"status":"running","toolbox_proxy_url":"https://tb.example","cores_max":1}`))
	}))
	defer regional.Close()
	home := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/11111111-1111-1111-1111-111111111111":
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"code":"NOT_FOUND","message":"not found","source":"DAYTONA_API"}`))
		case "/regions":
			_, _ = w.Write([]byte(`{"regions":[{"id":"region-a","name":"Region A","available":true,"api_base_url":"` + regional.URL + `"}],"default_region_id":null}`))
		default:
			t.Fatalf("unexpected path %s", r.URL.Path)
		}
	}))
	defer home.Close()
	resolved, err := ResolveSandbox(context.Background(), api.New(home.URL, "k", "test"), NewCatalogCache(), "11111111-1111-1111-1111-111111111111")
	require.NoError(t, err)
	require.Equal(t, 1, hits)
	require.Equal(t, regional.URL, resolved.Client.BaseURL)
}

func TestResolveSandbox_escapesLookupPath(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "/sandboxes/abc?def", r.URL.Path)
		assert.Empty(t, r.URL.RawQuery)
		_, _ = w.Write([]byte(`{"class":"linux","spot":false,"cpu":1,"id":"abc?def","image":{"id":"img_123","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"memory_mib":512,"name":"box","port_map":[],"region_id":"region-a","disk_mib":1024,"status":"running","toolbox_proxy_url":"https://tb.example","cores_max":1}`))
	}))
	defer server.Close()
	resolved, err := ResolveSandbox(context.Background(), api.New(server.URL, "k", "test"), NewCatalogCache(), "abc?def")
	require.NoError(t, err)
	require.Equal(t, "abc?def", resolved.Detail.Id)
}

func TestWaitHelpers(t *testing.T) {
	sandboxServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "/sandboxes/id", r.URL.Path)
		assert.Equal(t, url.Values{"wait": {"running"}, "timeout_ms": {"1000"}}, r.URL.Query())
		_, _ = w.Write([]byte(`{"class":"linux","spot":false,"cpu":1,"id":"id","image":{"id":"img_123","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"memory_mib":512,"name":"box","port_map":[],"region_id":"region-a","disk_mib":1024,"status":"running","toolbox_proxy_url":"https://tb.example","cores_max":1}`))
	}))
	defer sandboxServer.Close()
	imageServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "/images/img", r.URL.Path)
		assert.Equal(t, url.Values{"wait": {"ready"}, "timeout_ms": {"1000"}}, r.URL.Query())
		_, _ = w.Write([]byte(`{"anonymous":false,"region_ids":["region-a"],"class":"linux","created_at":"2026-09-13T00:00:00Z","id":"img","name":"img","origin":"registry","region_id":"region-a","retention":"permanent","source":{"type":"registry","ref":"python:3.12"},"status":"ready"}`))
	}))
	defer imageServer.Close()
	snapServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "/snapshots/snap", r.URL.Path)
		assert.Equal(t, url.Values{"wait": {"ready"}, "timeout_ms": {"1000"}}, r.URL.Query())
		_, _ = w.Write([]byte(`{"region_ids":["region-a"],"id":"11111111-1111-1111-1111-111111111111","kind":"full","name":"snap","region_id":"region-a","status":"ready"}`))
	}))
	defer snapServer.Close()
	timeout, err := ParseTimeout("1s")
	require.NoError(t, err)
	_, err = WaitForSandbox(context.Background(), api.New(sandboxServer.URL, "k", "test"), SandboxWaitOptions{ID: "id", Target: "running", Timeout: timeout})
	require.NoError(t, err)
	_, err = WaitForImage(context.Background(), api.New(imageServer.URL, "k", "test"), "img", timeout)
	require.NoError(t, err)
	_, err = WaitForSnapshot(context.Background(), api.New(snapServer.URL, "k", "test"), "snap", timeout)
	require.NoError(t, err)
	_, err = ParseTimeout("bad")
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}
func flagSet(t *testing.T, name string) *pflag.FlagSet {
	t.Helper()
	set := pflag.NewFlagSet("test", pflag.ContinueOnError)
	set.String(name, "", "")
	require.NoError(t, set.Set(name, "x"))
	return set
}
