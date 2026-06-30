// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"strings"

	apiclient "github.com/daytona/clients/api-client-go"
	apiclient_cli "github.com/daytona/clients/cli/apiclient"
	"github.com/daytona/clients/cli/config"
	"github.com/daytona/clients/cli/internal"
	toolboxclient "github.com/daytona/clients/toolbox-api-client-go"
)

type ProfileProvider interface {
	ActiveProfile() (config.Profile, error)
}

type ConfigProfileProvider struct{}

func (ConfigProfileProvider) ActiveProfile() (config.Profile, error) {
	cfg, err := config.GetConfig()
	if err != nil {
		return config.Profile{}, err
	}
	return cfg.GetActiveProfile()
}

// NewAPIClient creates a generated toolbox API client for the given sandbox.
// Requests are routed through the toolbox proxy at {proxyURL}/{sandboxId}.
//
// This factory keeps toolbox proxy resolution, cache usage, and CLI auth handling
// in the CLI toolbox package, matching the SDKs' direct generated-client path.
// Existing MCP file/process/git tools still use legacy main-API toolbox routes;
// should migrate them to this factory at some point.
func NewAPIClient(ctx context.Context, apiClient *apiclient.APIClient, sandbox *apiclient.Sandbox, source string) (*toolboxclient.APIClient, error) {
	return NewAPIClientWithProfileProvider(ctx, apiClient, sandbox, source, ConfigProfileProvider{})
}

// NewAPIClientWithProfileProvider is the testable variant of NewAPIClient.
// Production callers should usually use NewAPIClient.
func NewAPIClientWithProfileProvider(ctx context.Context, apiClient *apiclient.APIClient, sandbox *apiclient.Sandbox, source string, profiles ProfileProvider) (*toolboxclient.APIClient, error) {
	if sandbox == nil {
		return nil, fmt.Errorf("sandbox is required")
	}
	if sandbox.Id == "" {
		return nil, fmt.Errorf("sandbox ID is required")
	}
	if profiles == nil {
		profiles = ConfigProfileProvider{}
	}

	proxyURL, err := resolveProxyURL(ctx, apiClient, sandbox)
	if err != nil {
		return nil, err
	}

	toolboxURL := fmt.Sprintf("%s/%s", strings.TrimRight(proxyURL, "/"), sandbox.Id)
	parsedToolboxURL, err := url.Parse(toolboxURL)
	if err != nil {
		return nil, fmt.Errorf("invalid toolbox URL %q: %w", toolboxURL, err)
	}
	if parsedToolboxURL.Scheme == "" || parsedToolboxURL.Host == "" {
		return nil, fmt.Errorf("invalid toolbox URL %q: must include scheme and host", toolboxURL)
	}

	cfg := toolboxclient.NewConfiguration()
	cfg.Host = parsedToolboxURL.Host
	cfg.Scheme = parsedToolboxURL.Scheme
	cfg.HTTPClient = &http.Client{}

	cfg.Servers = toolboxclient.ServerConfigurations{
		{URL: fmt.Sprintf("%s://%s%s", cfg.Scheme, cfg.Host, parsedToolboxURL.Path)},
	}

	activeProfile, err := profiles.ActiveProfile()
	if err != nil {
		return nil, err
	}

	token := ""
	if activeProfile.Api.Key != nil {
		token = *activeProfile.Api.Key
	} else if activeProfile.Api.Token != nil {
		token = activeProfile.Api.Token.AccessToken
	}
	if token == "" {
		return nil, fmt.Errorf("no API credentials found; run `daytona login`")
	}

	cfg.AddDefaultHeader("Authorization", "Bearer "+token)
	if source != "" {
		cfg.AddDefaultHeader(apiclient_cli.DaytonaSourceHeader, source)
	}
	cfg.AddDefaultHeader("X-Daytona-CLI-Version", internal.Version)
	cfg.UserAgent = "daytona-cli/" + internal.Version

	if activeProfile.ActiveOrganizationId != nil {
		cfg.AddDefaultHeader("X-Daytona-Organization-ID", *activeProfile.ActiveOrganizationId)
	}

	return toolboxclient.NewAPIClient(cfg), nil
}

func resolveProxyURL(ctx context.Context, apiClient *apiclient.APIClient, sandbox *apiclient.Sandbox) (string, error) {
	if proxyURL := sandbox.GetToolboxProxyUrl(); proxyURL != "" {
		return proxyURL, nil
	}

	client := NewClient(apiClient)
	return client.getProxyURL(ctx, sandbox.Id, sandbox.Target)
}
