// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	apiclient_cli "github.com/daytona/clients/cli/apiclient"
	toolboxclient "github.com/daytona/clients/toolbox-api-client-go"
	"github.com/mark3labs/mcp-go/mcp"

	log "github.com/sirupsen/logrus"
)

type PreviewLinkArgs struct {
	Id          *string `json:"id,omitempty"`
	Port        *int32  `json:"port,omitempty"`
	CheckServer *bool   `json:"checkServer,omitempty"`
	Description *string `json:"description,omitempty"`
}

func GetPreviewLinkTool() mcp.Tool {
	return mcp.NewTool("preview_link",
		mcp.WithDescription("Generate accessible preview URLs for web applications running in the Daytona sandbox. Creates a secure tunnel to expose local ports externally without configuration. Validates if a server is actually running on the specified port and provides diagnostic information for troubleshooting. Supports custom descriptions and metadata for better organization of multiple services."),
		mcp.WithNumber("port", mcp.Required(), mcp.Description("Port to expose.")),
		mcp.WithString("description", mcp.Required(), mcp.Description("Description of the service.")),
		mcp.WithBoolean("checkServer", mcp.Required(), mcp.Description("Check if a server is running on the specified port.")),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox to generate the preview link for.")),
	)
}

func PreviewLink(ctx context.Context, request mcp.CallToolRequest, args PreviewLinkArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}

	if args.Port == nil {
		return toolResultError("port parameter is required")
	}

	checkServer := false
	if args.CheckServer != nil && *args.CheckServer {
		checkServer = *args.CheckServer
	}

	log.Infof("Generating preview link - port: %d", *args.Port)

	apiClient, err := apiclient_cli.GetApiClient(nil, daytonaMCPHeaders)
	if err != nil {
		return nil, err
	}

	var toolboxClient *toolboxclient.APIClient
	if checkServer {
		toolboxClient, errResult, err = getSandboxAndToolboxClient(ctx, sandboxID, true)
		if errResult != nil || err != nil {
			return errResult, err
		}
	}

	// Check if server is running on specified port
	if checkServer {
		log.Infof("Checking if server is running - port: %d", *args.Port)

		checkCmd := fmt.Sprintf("curl -s -o /dev/null -w '%%{http_code}' http://localhost:%d --max-time 2 || echo 'error'", *args.Port)
		result, _, apiErr := toolboxClient.ProcessAPI.ExecuteCommand(ctx).Request(*toolboxclient.NewExecuteRequest(checkCmd)).Execute()
		if apiErr != nil {
			return toolboxAPIError("Failed to check server", apiErr)
		}

		response := strings.TrimSpace(result.Result)
		if response == "error" || strings.HasPrefix(response, "0") {
			log.Infof("No server detected - port: %d", *args.Port)

			// Check what might be using the port
			psCmd := fmt.Sprintf("ps aux | grep ':%d' | grep -v grep || echo 'No process found'", *args.Port)
			psResult, _, psErr := toolboxClient.ProcessAPI.ExecuteCommand(ctx).Request(*toolboxclient.NewExecuteRequest(psCmd)).Execute()
			if psErr != nil {
				return toolboxAPIError("Failed to check processes", psErr)
			}

			return toolResultError(fmt.Sprintf("no server detected on port %d. Process info: %s", *args.Port, strings.TrimSpace(psResult.Result)))
		}
	}

	// Fetch preview URL
	previewURL, _, err := apiClient.SandboxAPI.GetPortPreviewUrl(ctx, sandboxID, float32(*args.Port)).Execute()
	if err != nil {
		return toolResultError(fmt.Sprintf("failed to get preview URL: %v", err))
	}

	// Test URL accessibility if requested
	var accessible bool
	var statusCode string
	if checkServer {
		checkCmd := fmt.Sprintf("curl -s -o /dev/null -w '%%{http_code}' %s --max-time 3 || echo 'error'", previewURL.Url)
		result, _, chkErr := toolboxClient.ProcessAPI.ExecuteCommand(ctx).Request(*toolboxclient.NewExecuteRequest(checkCmd)).Execute()
		if chkErr != nil {
			log.Errorf("Error checking preview URL: %v", chkErr)
		} else {
			response := strings.TrimSpace(result.Result)
			accessible = response != "error" && !strings.HasPrefix(response, "0")
			if _, err := strconv.Atoi(response); err == nil {
				statusCode = response
			}
		}
	}

	log.Infof("Preview link generated: %s", previewURL.Url)
	log.Infof("Accessible: %t", accessible)
	log.Infof("Status code: %s", statusCode)

	return mcp.NewToolResultText(fmt.Sprintf("Preview link generated: %s", previewURL.Url)), nil
}
