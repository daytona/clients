// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestToolboxPortList_routesThroughToolbox(t *testing.T) {
	stdout, _, err := runToolboxCLI(t, []string{"sandbox", "port", "list", "sbx"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodGet, path: "/test-tools/sbx/port", status: 200, auth: true, response: `{"ports":[3000,8080]}`},
	})
	require.NoError(t, err)
	items := requireJSONStdout(t, stdout)["items"].([]any)
	require.Len(t, items, 2)
}

func TestToolboxPortList_routesTaggedHyphenatedRegionIDs(t *testing.T) {
	var regionalHits int
	regionalURL := ""
	regional := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/abc-region-b":
			regionalHits++
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"id":"abc-region-b","class":"linux","spot":false,"name":"box","status":"running","region_id":"region-b","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"port_map":[],"disk_mib":1024,"memory_mib":512,"cpu":1,"cores_max":1,"toolbox_proxy_url":"` + regionalURL + `/test-tools"}`))
		case "/test-tools/abc-region-b/port":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"ports":[3000]}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer regional.Close()
	regionalURL = regional.URL
	home := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/regions":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"regions":[{"id":"region-b","name":"Region B","available":true,"parent_region_ids":["earth"],"api_base_url":"` + regional.URL + `"}],"default_region_id":null}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer home.Close()

	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", home.URL, "--api-key", "k", "-o", "json", "sandbox", "port", "list", "abc-region-b")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, 1, regionalHits)
	items := requireJSONStdout(t, stdout)["items"].([]any)
	require.Len(t, items, 1)
}

func TestToolboxPortList_fallsBackAcrossRegionsForBareUUID(t *testing.T) {
	id := "11111111-1111-1111-1111-111111111111"
	regionalURL := ""
	regional := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/" + id:
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"id":"` + id + `","class":"linux","spot":false,"name":"box","status":"running","region_id":"region-b","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"port_map":[],"disk_mib":1024,"memory_mib":512,"cpu":1,"cores_max":1,"toolbox_proxy_url":"` + regionalURL + `/test-tools"}`))
		case "/test-tools/" + id + "/port":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"ports":[8080]}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer regional.Close()
	regionalURL = regional.URL
	home := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/" + id:
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"code":"NOT_FOUND","message":"not found","source":"DAYTONA_API"}`))
		case "/regions":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"regions":[{"id":"region-b","name":"Region B","available":true,"parent_region_ids":["earth"],"api_base_url":"` + regional.URL + `"}],"default_region_id":null}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer home.Close()

	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", home.URL, "--api-key", "k", "-o", "json", "sandbox", "port", "list", id)
	require.Equal(t, 0, code, stderr)
	items := requireJSONStdout(t, stdout)["items"].([]any)
	require.Len(t, items, 1)
}

// exec takes no request body: stdin is the process's input, so --file is not
// a flag of exec at all.
func TestToolboxExec_hasNoFileFlag(t *testing.T) {
	api := newFakeAPI(t)
	_, stderr, code := run(t, cfgDir(t), nil,
		"--api-url", api.srv.URL,
		"--api-key", "k",
		"sandbox", "exec", "sbx", "--file", "-", "--", "cat",
	)
	require.Equal(t, cerr.ExitUsage, code)
	require.Contains(t, stderr, "unknown flag: --file")
	require.Empty(t, api.requests)
}
