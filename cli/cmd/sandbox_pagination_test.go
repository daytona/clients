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

func TestSandboxListAll_multiRegionTerminalCursorYieldsEveryRowOnce(t *testing.T) {
	type request struct {
		server string
		query  string
	}
	var requests []request
	var requestsMu sync.Mutex
	regionServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requestsMu.Lock()
		requests = append(requests, request{server: "b", query: r.URL.RawQuery})
		requestsMu.Unlock()
		require.Equal(t, http.MethodGet, r.Method)
		require.Equal(t, "/sandboxes", r.URL.Path)
		require.Equal(t, "Bearer k", r.Header.Get("Authorization"))
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Query().Get("cursor") {
		case "":
			_, _ = io.WriteString(w, `{"next_cursor":"b1","sandboxes":[{"class":"linux","cpu":1,"created_at":"2026-09-13T00:00:03Z","cursor":"b1","id":"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"memory_mib":512,"name":"b1","port_map":[],"region_id":"region-a","disk_mib":1024,"spot":false,"status":"running","cores_max":1}]}`)
		case "b1":
			_, _ = io.WriteString(w, `{"next_cursor":"b2","sandboxes":[{"class":"linux","cpu":1,"created_at":"2026-09-13T00:00:01Z","cursor":"b2","id":"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"memory_mib":512,"name":"b2","port_map":[],"region_id":"region-a","disk_mib":1024,"spot":false,"status":"running","cores_max":1}]}`)
		case "b2":
			_, _ = io.WriteString(w, `{"sandboxes":[{"class":"linux","cpu":1,"created_at":"2026-09-12T23:59:59Z","cursor":"b3","id":"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb3","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"memory_mib":512,"name":"b3","port_map":[],"region_id":"region-a","disk_mib":1024,"spot":false,"status":"running","cores_max":1}]}`)
		default:
			t.Fatalf("unexpected region-b cursor %q", r.URL.Query().Get("cursor"))
		}
	}))
	defer regionServer.Close()

	homeServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "Bearer k", r.Header.Get("Authorization"))
		require.Equal(t, "cli", r.Header.Get("X-Daytona-Source"))
		if writeSearchNotConfigured(w, r) {
			return
		}
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/regions":
			_, _ = io.WriteString(w, `{"regions":[{"id":"region-b","name":"Region B","available":true,"parent_region_ids":["earth"]},{"id":"region-a","name":"Region A","available":true,"parent_region_ids":["earth"],"api_base_url":"`+regionServer.URL+`"}],"default_region_id":null}`)
		case "/sandboxes":
			requestsMu.Lock()
			requests = append(requests, request{server: "a", query: r.URL.RawQuery})
			requestsMu.Unlock()
			switch r.URL.Query().Get("cursor") {
			case "":
				_, _ = io.WriteString(w, `{"sandboxes":[{"class":"linux","cpu":1,"created_at":"2026-09-13T00:00:06Z","cursor":"a1","id":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"memory_mib":512,"name":"a1","port_map":[],"region_id":"region-b","disk_mib":1024,"spot":false,"status":"running","cores_max":1},{"class":"linux","cpu":1,"created_at":"2026-09-13T00:00:05Z","cursor":"a2","id":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"memory_mib":512,"name":"a2","port_map":[],"region_id":"region-b","disk_mib":1024,"spot":false,"status":"running","cores_max":1},{"class":"linux","cpu":1,"created_at":"2026-09-13T00:00:02Z","cursor":"a3","id":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"memory_mib":512,"name":"a3","port_map":[],"region_id":"region-b","disk_mib":1024,"spot":false,"status":"running","cores_max":1}]}`)
			case "a2":
				_, _ = io.WriteString(w, `{"sandboxes":[{"class":"linux","cpu":1,"created_at":"2026-09-13T00:00:02Z","cursor":"a3","id":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"memory_mib":512,"name":"a3","port_map":[],"region_id":"region-b","disk_mib":1024,"spot":false,"status":"running","cores_max":1}]}`)
			default:
				t.Fatalf("unexpected region-a cursor %q", r.URL.Query().Get("cursor"))
			}
		default:
			t.Fatalf("unexpected home path %s", r.URL.Path)
		}
	}))
	defer homeServer.Close()

	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	root := NewRoot(stdout, stderr)
	root.SetArgs([]string{"--api-url", homeServer.URL, "--api-key", "k", "-o", "json", "sandbox", "list", "--all", "--limit", "2"})
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	require.NoError(t, root.ExecuteContext(ctx))

	var out struct {
		Items []struct {
			ID string `json:"id"`
		} `json:"items"`
	}
	require.NoError(t, json.Unmarshal(stdout.Bytes(), &out))
	require.Equal(t, []string{
		"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1",
		"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2",
		"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1",
		"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3",
		"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2",
		"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb3",
	}, []string{out.Items[0].ID, out.Items[1].ID, out.Items[2].ID, out.Items[3].ID, out.Items[4].ID, out.Items[5].ID})
	requestsMu.Lock()
	defer requestsMu.Unlock()
	aQueries := make([]string, 0, len(requests))
	bQueries := make([]string, 0, len(requests))
	for _, request := range requests {
		switch request.server {
		case "a":
			aQueries = append(aQueries, request.query)
		case "b":
			bQueries = append(bQueries, request.query)
		}
	}
	require.Equal(t, []string{"limit=2", "cursor=a2&limit=2"}, aQueries)
	require.Equal(t, []string{"limit=2", "limit=2", "cursor=b1&limit=2", "cursor=b2&limit=2"}, bQueries)
}
