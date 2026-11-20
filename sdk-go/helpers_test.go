// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

// Shared wire-test helpers:
//   fake := newFakeServer(t, map[string]http.HandlerFunc{
//       "GET /sandboxes/sbx": jsonHandler(t, sandboxFixture("sbx")),
//   })
//   client := newTestClient(t, fake.Server)
//   requests := fake.requests() // independent request snapshots
//
// Register handlers before using the server. Routes use http.ServeMux syntax.
// sandboxFixture returns a fresh, complete minimal detail, with an inert toolbox
// URL; replace toolbox_proxy_url with fake.Server.URL+"/toolbox" for toolbox tests.
// newTestClient isolates environment with t.Setenv: do not use it with t.Parallel.

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"

	"github.com/stretchr/testify/require"
)

type recordedRequest struct {
	Method string
	Path   string
	Query  url.Values
	Header http.Header
	Body   []byte
}

type fakeServer struct {
	Server   *httptest.Server
	mu       sync.Mutex
	recorded []recordedRequest
}

func newFakeServer(t *testing.T, routes map[string]http.HandlerFunc) *fakeServer {
	t.Helper()
	fake := &fakeServer{}
	mux := http.NewServeMux()
	for route, handler := range routes {
		mux.HandleFunc(route, handler)
	}
	fake.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("read fake request: %v", err)
			return
		}
		if closeErr := r.Body.Close(); closeErr != nil {
			t.Errorf("close fake request: %v", closeErr)
			return
		}
		fake.mu.Lock()
		fake.recorded = append(fake.recorded, recordedRequest{
			Method: r.Method, Path: r.URL.EscapedPath(), Query: r.URL.Query(), Header: r.Header.Clone(), Body: body,
		})
		fake.mu.Unlock()
		r.Body = io.NopCloser(strings.NewReader(string(body)))
		mux.ServeHTTP(w, r)
	}))
	t.Cleanup(fake.Server.Close)
	return fake
}

func (f *fakeServer) requests() []recordedRequest {
	f.mu.Lock()
	defer f.mu.Unlock()
	result := make([]recordedRequest, len(f.recorded))
	for i, request := range f.recorded {
		result[i] = request
		result[i].Header = request.Header.Clone()
		result[i].Body = append([]byte(nil), request.Body...)
		result[i].Query = make(url.Values)
		for key, values := range request.Query {
			result[i].Query[key] = append([]string(nil), values...)
		}
	}
	return result
}

func writeJSON(t *testing.T, w http.ResponseWriter, value any) {
	t.Helper()
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(value); err != nil {
		t.Errorf("write fake JSON: %v", err)
	}
}

func jsonHandler(t *testing.T, value any) http.HandlerFunc {
	t.Helper()
	return func(w http.ResponseWriter, _ *http.Request) { writeJSON(t, w, value) }
}

func sandboxFixture(id string) map[string]any {
	return map[string]any{
		"id": id, "name": "example", "class": "linux", "status": "running",
		"cpu": 1, "cores_max": 2, "memory_mib": 1024, "disk_mib": 2048,
		"region_id": "region-a", "port_map": []any{}, "labels": map[string]string{},
		"toolbox_proxy_url": "http://toolbox.invalid",
	}
}

func newTestClient(t *testing.T, server *httptest.Server, opts ...Option) *Client {
	t.Helper()
	t.Setenv("DAYTONA_V2_API_URL", server.URL)
	t.Setenv("DAYTONA_API_KEY", strings.Join([]string{"test", "key"}, "-"))
	t.Setenv("DAYTONA_JWT_TOKEN", "")
	t.Setenv("DAYTONA_PROJECT_ID", "")
	t.Setenv("DAYTONA_API_URL", "")
	client, err := New(opts...)
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, client.Close()) })
	return client
}
