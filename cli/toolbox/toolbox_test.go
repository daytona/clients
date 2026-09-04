// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	apiclient "github.com/daytona/clients/api-client-go"
	"github.com/daytona/clients/cli/config"
)

type pathRecorder struct {
	mu    sync.Mutex
	paths []string
}

func (r *pathRecorder) record(path string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.paths = append(r.paths, path)
}

func (r *pathRecorder) snapshot() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string(nil), r.paths...)
}

func (r *pathRecorder) contains(path string) bool {
	for _, p := range r.snapshot() {
		if p == path {
			return true
		}
	}
	return false
}

// seedProfileConfig points DAYTONA_CONFIG_DIR at a temp dir holding a single
// active profile, and clears the env credentials that would otherwise take
// precedence in GetActiveProfile.
func seedProfileConfig(t *testing.T) string {
	t.Helper()

	dir := t.TempDir()
	t.Setenv("DAYTONA_CONFIG_DIR", dir)
	t.Setenv(config.DAYTONA_API_URL_ENV_VAR, "")
	t.Setenv(config.DAYTONA_API_KEY_ENV_VAR, "")

	cfg := config.Config{
		ActiveProfileId: "default",
		Profiles: []config.Profile{{
			Id:   "default",
			Name: "default",
			Api: config.ServerApi{
				Url: "https://api.example.test",
				Key: strPtr("test-api-key"),
			},
		}},
	}

	contents, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		t.Fatalf("marshal seed config: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "config.json"), contents, 0600); err != nil {
		t.Fatalf("write seed config: %v", err)
	}

	return dir
}

func newProxyServer(t *testing.T, recorder *pathRecorder) *httptest.Server {
	t.Helper()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		recorder.record(r.URL.Path)
		if r.URL.Path == "/sbx123/process/execute" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"exitCode":0,"result":"ok"}`))
			return
		}
		http.NotFound(w, r)
	}))
	t.Cleanup(server.Close)

	return server
}

// newAPIServer stands in for the main API. proxyURLResponse is served from the
// toolbox-proxy-url endpoint when non-empty; otherwise that route 404s.
func newAPIServer(t *testing.T, recorder *pathRecorder, proxyURLResponse string) *httptest.Server {
	t.Helper()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		recorder.record(r.URL.Path)
		if r.URL.Path == "/sandbox/sbx123/toolbox-proxy-url" && proxyURLResponse != "" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"url":"` + proxyURLResponse + `"}`))
			return
		}
		http.NotFound(w, r)
	}))
	t.Cleanup(server.Close)

	return server
}

func newTestAPIClient(baseURL string) *apiclient.APIClient {
	cfg := apiclient.NewConfiguration()
	cfg.Servers = apiclient.ServerConfigurations{{URL: baseURL}}
	return apiclient.NewAPIClient(cfg)
}

const toolboxProxyURLPath = "/sandbox/sbx123/toolbox-proxy-url"

func TestExecuteCommandUsesSandboxProxyURL(t *testing.T) {
	seedProfileConfig(t)

	proxyPaths := &pathRecorder{}
	apiPaths := &pathRecorder{}
	proxyServer := newProxyServer(t, proxyPaths)
	apiServer := newAPIServer(t, apiPaths, proxyServer.URL)

	sandbox := &apiclient.Sandbox{Id: "sbx123", Target: "us", ToolboxProxyUrl: proxyServer.URL}

	response, err := NewClient(newTestAPIClient(apiServer.URL)).
		ExecuteCommand(context.Background(), sandbox, ExecuteRequest{Command: "echo ok"})
	if err != nil {
		t.Fatalf("ExecuteCommand() error = %v", err)
	}
	if response.Result != "ok" {
		t.Fatalf("expected result %q, got %q", "ok", response.Result)
	}

	if !proxyPaths.contains("/sbx123/process/execute") {
		t.Fatalf("expected execute request on the sandbox proxy, got paths %v", proxyPaths.snapshot())
	}
	if apiPaths.contains(toolboxProxyURLPath) {
		t.Fatalf("expected no toolbox-proxy-url request, got paths %v", apiPaths.snapshot())
	}
	if got := len(apiPaths.snapshot()); got != 0 {
		t.Fatalf("expected no main API requests, got paths %v", apiPaths.snapshot())
	}
}

func TestExecuteCommandFallsBackWhenSandboxProxyURLEmpty(t *testing.T) {
	seedProfileConfig(t)

	proxyPaths := &pathRecorder{}
	apiPaths := &pathRecorder{}
	proxyServer := newProxyServer(t, proxyPaths)
	apiServer := newAPIServer(t, apiPaths, proxyServer.URL)

	sandbox := &apiclient.Sandbox{Id: "sbx123", Target: "us", ToolboxProxyUrl: ""}

	response, err := NewClient(newTestAPIClient(apiServer.URL)).
		ExecuteCommand(context.Background(), sandbox, ExecuteRequest{Command: "echo ok"})
	if err != nil {
		t.Fatalf("ExecuteCommand() error = %v", err)
	}
	if response.Result != "ok" {
		t.Fatalf("expected result %q, got %q", "ok", response.Result)
	}

	if !apiPaths.contains(toolboxProxyURLPath) {
		t.Fatalf("expected fallback toolbox-proxy-url request, got paths %v", apiPaths.snapshot())
	}
	if !proxyPaths.contains("/sbx123/process/execute") {
		t.Fatalf("expected execute request on the resolved proxy, got paths %v", proxyPaths.snapshot())
	}
}

func TestFallbackRejectsResponseWithoutURL(t *testing.T) {
	tests := []struct {
		name string
		body string
	}{
		{name: "empty body", body: ""},
		{name: "null body", body: "null"},
		{name: "empty url", body: `{"url":""}`},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			apiServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write([]byte(test.body))
			}))
			t.Cleanup(apiServer.Close)

			sandbox := &apiclient.Sandbox{Id: "sbx123", Target: "us", ToolboxProxyUrl: ""}

			got, err := NewClient(newTestAPIClient(apiServer.URL)).getProxyURL(context.Background(), sandbox)
			if err == nil {
				t.Fatalf("expected error, got %q", got)
			}
			if !strings.Contains(err.Error(), "did not contain a URL") {
				t.Fatalf("expected missing-URL error, got %v", err)
			}
		})
	}
}

func TestProxyURLNotPersistedToConfig(t *testing.T) {
	configDir := seedProfileConfig(t)

	proxyPaths := &pathRecorder{}
	apiPaths := &pathRecorder{}
	proxyServer := newProxyServer(t, proxyPaths)
	apiServer := newAPIServer(t, apiPaths, proxyServer.URL)

	sandbox := &apiclient.Sandbox{Id: "sbx123", Target: "us", ToolboxProxyUrl: ""}

	if _, err := NewClient(newTestAPIClient(apiServer.URL)).
		ExecuteCommand(context.Background(), sandbox, ExecuteRequest{Command: "echo ok"}); err != nil {
		t.Fatalf("ExecuteCommand() error = %v", err)
	}

	contents, err := os.ReadFile(filepath.Join(configDir, "config.json"))
	if err != nil {
		t.Fatalf("read config: %v", err)
	}

	if strings.Contains(strings.ToLower(string(contents)), "toolboxproxyurl") {
		t.Fatalf("expected no proxy URL key in config.json, got:\n%s", contents)
	}
	if strings.Contains(string(contents), proxyServer.URL) {
		t.Fatalf("expected no proxy URL value in config.json, got:\n%s", contents)
	}
}

func TestProxyURLRequiresHTTPS(t *testing.T) {
	tests := []struct {
		name     string
		proxyURL string
		wantErr  bool
	}{
		{name: "https host", proxyURL: "https://proxy.example.test/toolbox", wantErr: false},
		{name: "http localhost", proxyURL: "http://localhost:3000", wantErr: false},
		{name: "http loopback ipv4", proxyURL: "http://127.0.0.1:3000", wantErr: false},
		{name: "http loopback ipv6", proxyURL: "http://[::1]:3000", wantErr: false},
		{name: "http public host", proxyURL: "http://example.com", wantErr: true},
		{name: "missing scheme", proxyURL: "proxy.example.test/toolbox", wantErr: true},
		{name: "https without host", proxyURL: "https://:443", wantErr: true},
		{name: "http without host", proxyURL: "http://:80", wantErr: true},
		{name: "query string", proxyURL: "https://proxy.example.test/toolbox?tenant=acme", wantErr: true},
		{name: "empty query string", proxyURL: "https://proxy.example.test/toolbox?", wantErr: true},
		{name: "fragment", proxyURL: "https://proxy.example.test/toolbox#frag", wantErr: true},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			sandbox := &apiclient.Sandbox{Id: "sbx123", Target: "us", ToolboxProxyUrl: test.proxyURL}

			got, err := NewClient(nil).getProxyURL(context.Background(), sandbox)
			if test.wantErr {
				if err == nil {
					t.Fatalf("expected error for %q, got %q", test.proxyURL, got)
				}
				return
			}
			if err != nil {
				t.Fatalf("getProxyURL(%q) error = %v", test.proxyURL, err)
			}
			if got != test.proxyURL {
				t.Fatalf("expected %q, got %q", test.proxyURL, got)
			}
		})
	}
}
