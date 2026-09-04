// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"

	apiclient "github.com/daytona/clients/api-client-go"
	"github.com/daytona/clients/cli/config"
)

type ExecuteRequest struct {
	Command string   `json:"command"`
	Cwd     *string  `json:"cwd,omitempty"`
	Timeout *float32 `json:"timeout,omitempty"`
}

type ExecuteResponse struct {
	ExitCode float32 `json:"exitCode"`
	Result   string  `json:"result"`
}

type Client struct {
	apiClient *apiclient.APIClient
}

func NewClient(apiClient *apiclient.APIClient) *Client {
	return &Client{
		apiClient: apiClient,
	}
}

// getProxyURL returns the toolbox proxy URL for a sandbox.
//
// toolboxProxyUrl is a required field on the Sandbox schema, so callers that
// already hold a sandbox have the value in hand and no further request is
// needed. The dedicated endpoint is kept as a fallback for API servers that do
// not populate the field.
func (c *Client) getProxyURL(ctx context.Context, sandbox *apiclient.Sandbox) (string, error) {
	if sandbox == nil {
		return "", fmt.Errorf("sandbox is required")
	}

	if proxyURL := sandbox.GetToolboxProxyUrl(); proxyURL != "" {
		if err := requireValidProxyURL(proxyURL); err != nil {
			return "", err
		}

		return proxyURL, nil
	}

	if c == nil || c.apiClient == nil {
		return "", fmt.Errorf("api client is required when sandbox has no toolbox proxy URL")
	}

	toolboxProxyUrl, _, err := c.apiClient.SandboxAPI.GetToolboxProxyUrl(ctx, sandbox.Id).Execute()
	if err != nil {
		return "", fmt.Errorf("failed to get toolbox proxy URL: %w", err)
	}
	if toolboxProxyUrl == nil || toolboxProxyUrl.Url == "" {
		return "", fmt.Errorf("failed to get toolbox proxy URL: response did not contain a URL")
	}

	if err := requireValidProxyURL(toolboxProxyUrl.Url); err != nil {
		return "", err
	}

	return toolboxProxyUrl.Url, nil
}

// requireValidProxyURL enforces https for toolbox proxy endpoints. Plain http
// is permitted only for loopback hosts so local development keeps working.
//
// A query or fragment is rejected because callers append the sandbox path to
// this URL, which would otherwise land after the delimiter and silently route
// the request somewhere else.
func requireValidProxyURL(rawURL string) error {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return fmt.Errorf("invalid toolbox URL %q: %w", rawURL, err)
	}

	return requireValidParsedProxyURL(parsed, rawURL)
}

func requireValidParsedProxyURL(parsed *url.URL, rawURL string) error {
	if parsed.Scheme == "" || parsed.Hostname() == "" {
		return fmt.Errorf("invalid toolbox URL %q: must include scheme and host", rawURL)
	}

	if parsed.RawQuery != "" || parsed.ForceQuery || parsed.Fragment != "" {
		return fmt.Errorf("invalid toolbox URL %q: must not include a query string or fragment", rawURL)
	}

	switch strings.ToLower(parsed.Scheme) {
	case "https":
		return nil
	case "http":
		if isLoopbackHost(parsed.Hostname()) {
			return nil
		}
	}

	return fmt.Errorf(
		"invalid toolbox URL %q: scheme %q is not supported for host %q; https is required, and http is accepted only for loopback hosts",
		rawURL, parsed.Scheme, parsed.Host,
	)
}

func isLoopbackHost(host string) bool {
	if strings.EqualFold(host, "localhost") {
		return true
	}
	if ip := net.ParseIP(host); ip != nil {
		return ip.IsLoopback()
	}

	return false
}

func (c *Client) ExecuteCommand(ctx context.Context, sandbox *apiclient.Sandbox, request ExecuteRequest) (*ExecuteResponse, error) {
	proxyURL, err := c.getProxyURL(ctx, sandbox)
	if err != nil {
		return nil, err
	}

	return c.executeCommandViaProxy(ctx, proxyURL, sandbox.Id, request)
}

// TODO: migrate this manual process client to NewAPIClient in a follow-up PR.
func (c *Client) executeCommandViaProxy(ctx context.Context, proxyURL, sandboxId string, request ExecuteRequest) (*ExecuteResponse, error) {
	// Build the URL: {proxyUrl}/{sandboxId}/process/execute
	url := fmt.Sprintf("%s/%s/process/execute", strings.TrimSuffix(proxyURL, "/"), sandboxId)

	body, err := json.Marshal(request)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")

	cfg, err := config.GetConfig()
	if err != nil {
		return nil, err
	}

	activeProfile, err := cfg.GetActiveProfile()
	if err != nil {
		return nil, err
	}

	if activeProfile.Api.Key != nil {
		req.Header.Set("Authorization", "Bearer "+*activeProfile.Api.Key)
	} else if activeProfile.Api.Token != nil {
		req.Header.Set("Authorization", "Bearer "+activeProfile.Api.Token.AccessToken)
	}

	if activeProfile.ActiveOrganizationId != nil {
		req.Header.Set("X-Daytona-Organization-ID", *activeProfile.ActiveOrganizationId)
	}

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed to execute request: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("request failed with status %d: %s", resp.StatusCode, string(respBody))
	}

	var response ExecuteResponse
	if err := json.Unmarshal(respBody, &response); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	return &response, nil
}
