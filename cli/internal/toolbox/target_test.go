// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	genapi "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestTargetFromDetail_scopesToolboxURLLikeSDKs(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name    string
		id      string
		baseURL string
		want    string
	}{
		{name: "plain toolbox base", id: "sbx", baseURL: "https://tb.example/test-tools", want: "https://tb.example/test-tools/sbx"},
		{name: "host only base", id: "sbx", baseURL: "https://tb.eu.example.com", want: "https://tb.eu.example.com/sbx"},
		{name: "trailing slash", id: "sbx", baseURL: "https://tb.example/test-tools/", want: "https://tb.example/test-tools/sbx"},
		{name: "camel placeholder", id: "sbx", baseURL: "https://tb.example/test-tools/{sandboxId}", want: "https://tb.example/test-tools/sbx"},
		{name: "snake placeholder", id: "sbx", baseURL: "https://tb.example/test-tools/{sandbox_id}", want: "https://tb.example/test-tools/sbx"},
		{name: "percent encodes sandbox id", id: "seg A/B", baseURL: "https://tb.example/test-tools/{sandboxId}", want: "https://tb.example/test-tools/seg%20A%2FB"},
	}
	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			target, err := TargetFromDetail(genapi.Sandbox{Id: tt.id, Class: "linux", ToolboxProxyUrl: strPtr(tt.baseURL), SshGatewayHost: strPtr("ssh.example"), SshGatewayPort: int32Ptr(2200)})
			require.NoError(t, err)
			require.Equal(t, tt.want, target.ToolboxBaseURL)
			require.Equal(t, "ssh.example", target.SSHHost)
			require.Equal(t, 2200, target.SSHPort)
		})
	}
}

func TestTargetFromDetail_rejectsPlaceholderOutsidePath(t *testing.T) {
	t.Parallel()
	templates := []string{
		"https://{sandboxId}.example.com/test-tools",
		"https://tb.example.com:{sandbox_id}/test-tools",
		"https://tb.example.com/test-tools?id={sandboxId}",
		"https://tb.example.com/test-tools#{sandbox_id}",
		"https://tb.example.com?x=/test-tools/{sandboxId}",
		"https://tb.example.com#/test-tools/{sandbox_id}",
	}
	for _, template := range templates {
		template := template
		t.Run(template, func(t *testing.T) {
			t.Parallel()
			_, err := TargetFromDetail(genapi.Sandbox{Id: "sbx", Class: "linux", ToolboxProxyUrl: strPtr(template)})
			require.Error(t, err)
			require.Contains(t, err.Error(), "only supported in the path")
		})
	}
}

func TestResolveTarget_fallsBackToConfigSSHGateway(t *testing.T) {
	t.Parallel()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/sbx":
			_ = json.NewEncoder(w).Encode(genapi.Sandbox{Id: "sbx", Class: "linux", ToolboxProxyUrl: strPtr("https://tb.example")})
		case "/config":
			_ = json.NewEncoder(w).Encode(map[string]any{"ssh_gateway": map[string]any{"host": "ssh.fallback", "port": 2223}})
		default:
			t.Fatalf("unexpected path %s", r.URL.Path)
		}
	}))
	defer server.Close()
	target, err := TargetFromDetail(genapi.Sandbox{Id: "sbx", Class: "linux", ToolboxProxyUrl: strPtr("https://tb.example")})
	if err != nil {
		t.Fatal(err)
	}
	if err := ResolveSSHGateway(context.Background(), api.New(server.URL, "tok", "test"), target); err != nil {
		t.Fatal(err)
	}
	if target.SSHHost != "ssh.fallback" || target.SSHPort != 2223 {
		t.Fatalf("ssh=%s:%d", target.SSHHost, target.SSHPort)
	}
}

func TestTargetFromDetail_emptyToolboxURL(t *testing.T) {
	t.Parallel()
	_, err := TargetFromDetail(genapi.Sandbox{Id: "sbx", Class: "linux", ToolboxProxyUrl: strPtr("")})
	if err == nil {
		t.Fatal("expected error for empty toolbox URL")
	}
	var ce *cerr.Error
	if !errors.As(err, &ce) || ce.Code != cerr.ExitConflict {
		t.Fatalf("err=%v", err)
	}
}

func TestResolveSSHGateway_alreadySet(t *testing.T) {
	t.Parallel()
	target := &Target{SSHHost: "already.set", SSHPort: 1234}
	if err := ResolveSSHGateway(context.Background(), api.New("http://unused", "", "test"), target); err != nil {
		t.Fatal(err)
	}
	if target.SSHHost != "already.set" || target.SSHPort != 1234 {
		t.Fatalf("ssh=%s:%d", target.SSHHost, target.SSHPort)
	}
}

func TestResolveSSHGateway_rejectsConfigWithoutUsablePort(t *testing.T) {
	t.Parallel()
	// Given a published host without a usable port.
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"ssh_gateway": map[string]any{"host": "gw.example", "port": 0}})
	}))
	defer server.Close()
	target := &Target{}
	// When resolving the SSH gateway.
	err := ResolveSSHGateway(context.Background(), api.New(server.URL, "tok", "test"), target)
	// Then fail without guessing a port.
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
}

func TestResolveSSHGateway_usesConfigWhenDetailOmitsPort(t *testing.T) {
	t.Parallel()
	// Given sandbox details with a host but no port and a complete config gateway.
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if _, err := w.Write([]byte(`{"ssh_gateway":{"host":"gw.example","port":2223}}`)); err != nil {
			t.Error(err)
		}
	}))
	defer server.Close()
	target, err := TargetFromDetail(genapi.Sandbox{Id: "sbx", ToolboxProxyUrl: strPtr("https://tb.example"), SshGatewayHost: strPtr("ssh.example")})
	require.NoError(t, err)
	// When resolving the SSH gateway.
	err = ResolveSSHGateway(context.Background(), api.New(server.URL, "tok", "test"), target)
	// Then use the complete published address.
	require.NoError(t, err)
	require.Equal(t, "gw.example", target.SSHHost)
	require.Equal(t, 2223, target.SSHPort)
}

func TestResolveSSHGateway_configErrorsSurface(t *testing.T) {
	t.Parallel()
	var sawAuth string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		sawAuth = r.Header.Get("Authorization")
		w.WriteHeader(http.StatusNotFound)
	}))
	defer server.Close()
	target := &Target{Detail: genapi.Sandbox{Id: "sbx"}}
	err := ResolveSSHGateway(context.Background(), api.New(server.URL, "tok", "test"), target)
	if cerr.ExitCode(err) != cerr.ExitNotFound {
		t.Fatalf("a failing GET /config must surface, got %v", err)
	}
	if sawAuth != "" {
		t.Fatalf("/config is unauthenticated; credential leaked: %q", sawAuth)
	}
	if target.SSHHost != "" {
		t.Fatalf("host should be empty, got %q", target.SSHHost)
	}
}

func TestResolveSSHGateway_noGatewayPublishedIsConflict(t *testing.T) {
	t.Parallel()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		// The identity fields are illustrative of a stamped api; the CLI does not require them at runtime.
		_, _ = w.Write([]byte(`{"billing_enabled":false,"version":"2.0.0-alpha.1","build_sha":"0123456789abcdef0123456789abcdef01234567","built_at":"2026-10-02T14:03:11Z"}`))
	}))
	defer server.Close()
	target := &Target{Detail: genapi.Sandbox{Id: "sbx"}}
	err := ResolveSSHGateway(context.Background(), api.New(server.URL, "", "test"), target)
	if cerr.ExitCode(err) != cerr.ExitConflict {
		t.Fatalf("want exit 6 when no gateway is published, got %v", err)
	}
}

func int32Ptr(v int32) *int32 { return &v }
func strPtr(v string) *string { return &v }
