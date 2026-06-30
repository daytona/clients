// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	apiclient "github.com/daytona/clients/api-client-go"
	"github.com/daytona/clients/cli/config"
)

type staticProfileProvider struct {
	profile config.Profile
}

func (p staticProfileProvider) ActiveProfile() (config.Profile, error) {
	return p.profile, nil
}

func TestNewAPIClientConfiguresSandboxBaseURLAndHeaders(t *testing.T) {
	var receivedAuth string
	proxyServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedAuth = r.Header.Get("Authorization")
		if r.URL.Path == "/toolbox/sbx123/computeruse/process-status" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"status":"running"}`))
			return
		}
		http.NotFound(w, r)
	}))
	defer proxyServer.Close()

	apiCfg := apiclient.NewConfiguration()
	apiCfg.Servers = apiclient.ServerConfigurations{{URL: "https://api.example.test"}}
	apiClient := apiclient.NewAPIClient(apiCfg)

	sandbox := &apiclient.Sandbox{Id: "sbx123", Target: "us", ToolboxProxyUrl: proxyServer.URL + "/toolbox"}
	toolboxClient, err := NewAPIClientWithProfileProvider(
		context.Background(),
		apiClient,
		sandbox,
		"daytona-mcp",
		staticProfileProvider{
			profile: config.Profile{
				Api: config.ServerApi{
					Key: strPtr("test-api-key"),
				},
			},
		},
	)
	if err != nil {
		t.Fatalf("NewAPIClient() error = %v", err)
	}

	clientCfg := toolboxClient.GetConfig()
	if clientCfg.Servers[0].URL == "" || !strings.Contains(clientCfg.Servers[0].URL, "sbx123") {
		t.Fatalf("expected server URL to include sandbox ID, got %q", clientCfg.Servers[0].URL)
	}
	if clientCfg.DefaultHeader["Authorization"] != "Bearer test-api-key" {
		t.Fatalf("expected Authorization header, got %q", clientCfg.DefaultHeader["Authorization"])
	}
	if clientCfg.DefaultHeader["X-Daytona-Source"] != "daytona-mcp" {
		t.Fatalf("expected X-Daytona-Source daytona-mcp, got %q", clientCfg.DefaultHeader["X-Daytona-Source"])
	}

	_, _, err = toolboxClient.ComputerUseAPI.GetComputerUseStatus(context.Background()).Execute()
	if err != nil {
		t.Fatalf("GetComputerUseStatus() error = %v", err)
	}
	if receivedAuth != "Bearer test-api-key" {
		t.Fatalf("expected proxy request auth Bearer test-api-key, got %q", receivedAuth)
	}
}

func TestNewAPIClientUsesTokenCredentialsAndOrganizationHeader(t *testing.T) {
	var receivedHeaders http.Header
	proxyServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedHeaders = r.Header.Clone()
		if r.URL.Path == "/toolbox/sbx123/computeruse/process-status" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"status":"running"}`))
			return
		}
		http.NotFound(w, r)
	}))
	defer proxyServer.Close()

	apiCfg := apiclient.NewConfiguration()
	apiCfg.Servers = apiclient.ServerConfigurations{{URL: "https://api.example.test"}}
	apiClient := apiclient.NewAPIClient(apiCfg)

	orgID := "org-123"
	sandbox := &apiclient.Sandbox{Id: "sbx123", Target: "us", ToolboxProxyUrl: proxyServer.URL + "/toolbox"}
	toolboxClient, err := NewAPIClientWithProfileProvider(
		context.Background(),
		apiClient,
		sandbox,
		"daytona-mcp",
		staticProfileProvider{
			profile: config.Profile{
				Api: config.ServerApi{
					Token: &config.Token{AccessToken: "oauth-access-token"},
				},
				ActiveOrganizationId: &orgID,
			},
		},
	)
	if err != nil {
		t.Fatalf("NewAPIClient() error = %v", err)
	}

	_, _, err = toolboxClient.ComputerUseAPI.GetComputerUseStatus(context.Background()).Execute()
	if err != nil {
		t.Fatalf("GetComputerUseStatus() error = %v", err)
	}

	if receivedHeaders.Get("Authorization") != "Bearer oauth-access-token" {
		t.Fatalf("expected OAuth Authorization header, got %q", receivedHeaders.Get("Authorization"))
	}
	if receivedHeaders.Get("X-Daytona-Organization-ID") != "org-123" {
		t.Fatalf("expected organization header org-123, got %q", receivedHeaders.Get("X-Daytona-Organization-ID"))
	}
	if receivedHeaders.Get("X-Daytona-Source") != "daytona-mcp" {
		t.Fatalf("expected source header daytona-mcp, got %q", receivedHeaders.Get("X-Daytona-Source"))
	}
	if receivedHeaders.Get("X-Daytona-CLI-Version") == "" {
		t.Fatal("expected non-empty X-Daytona-CLI-Version header")
	}
	if !strings.HasPrefix(receivedHeaders.Get("User-Agent"), "daytona-cli/") {
		t.Fatalf("expected daytona-cli User-Agent, got %q", receivedHeaders.Get("User-Agent"))
	}
}

func TestNewAPIClientRejectsProfileWithoutCredentials(t *testing.T) {
	apiCfg := apiclient.NewConfiguration()
	apiCfg.Servers = apiclient.ServerConfigurations{{URL: "https://api.example.test"}}
	apiClient := apiclient.NewAPIClient(apiCfg)

	sandbox := &apiclient.Sandbox{Id: "sbx123", ToolboxProxyUrl: "https://proxy.example.test/toolbox"}
	toolboxClient, err := NewAPIClientWithProfileProvider(
		context.Background(),
		apiClient,
		sandbox,
		"daytona-mcp",
		staticProfileProvider{profile: config.Profile{Api: config.ServerApi{}}},
	)
	if toolboxClient != nil {
		t.Fatalf("expected nil client, got %#v", toolboxClient)
	}
	if err == nil || !strings.Contains(err.Error(), "no API credentials found; run `daytona login`") {
		t.Fatalf("expected missing credentials error, got %v", err)
	}
}

func strPtr(value string) *string {
	return &value
}
