// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"

	apiclient "github.com/daytona/clients/api-client-go"
	apiclient_cli "github.com/daytona/clients/cli/apiclient"
	"github.com/daytona/clients/cli/toolbox"
	toolboxclient "github.com/daytona/clients/toolbox-api-client-go"
	"github.com/mark3labs/mcp-go/mcp"
)

var daytonaMCPHeaders map[string]string = map[string]string{
	apiclient_cli.DaytonaSourceHeader: "daytona-mcp",
}

type SandboxIdArgs struct {
	Id *string `json:"id,omitempty"`
}

func int32FromInt(value int, fieldName string) (int32, *mcp.CallToolResult, error) {
	if value < math.MinInt32 || value > math.MaxInt32 {
		errResult, err := toolResultError(fmt.Sprintf("%s must be between %d and %d", fieldName, math.MinInt32, math.MaxInt32))
		return 0, errResult, err
	}
	return int32(value), nil, nil
}

func int32FromIntNonNegative(value int, fieldName string) (int32, *mcp.CallToolResult, error) {
	if value < 0 {
		errResult, err := toolResultError(fmt.Sprintf("%s must be non-negative", fieldName))
		return 0, errResult, err
	}
	return int32FromInt(value, fieldName)
}

func requireSandboxID(id *string) (string, *mcp.CallToolResult, error) {
	if id == nil || strings.TrimSpace(*id) == "" {
		errResult, err := toolResultError("Sandbox ID is required")
		return "", errResult, err
	}
	return strings.TrimSpace(*id), nil, nil
}

func getSandboxAndToolboxClient(ctx context.Context, sandboxID string, requireStarted bool) (*toolboxclient.APIClient, *mcp.CallToolResult, error) {
	apiClient, err := apiclient_cli.GetApiClient(nil, daytonaMCPHeaders)
	if err != nil {
		return nil, nil, err
	}

	sandbox, _, err := apiClient.SandboxAPI.GetSandbox(ctx, sandboxID).Execute()
	if err != nil {
		errResult, toolErr := toolResultError(fmt.Sprintf("Failed to get sandbox %s: %v", sandboxID, err))
		return nil, errResult, toolErr
	}

	if requireStarted && sandbox.GetState() != apiclient.SANDBOXSTATE_STARTED {
		errResult, toolErr := toolResultError(fmt.Sprintf("Sandbox %s is not started (state: %s)", sandboxID, sandbox.GetState()))
		return nil, errResult, toolErr
	}

	toolboxClient, err := toolbox.NewAPIClient(ctx, apiClient, sandbox, daytonaMCPHeaders[apiclient_cli.DaytonaSourceHeader])
	if err != nil {
		errResult, toolErr := toolResultError(fmt.Sprintf("Failed to create toolbox client: %v", err))
		return nil, errResult, toolErr
	}

	return toolboxClient, nil, nil
}

func toolResultJSON(value any) (*mcp.CallToolResult, error) {
	resultJSON, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return toolResultError(fmt.Sprintf("Failed to marshal response: %v", err))
	}
	return mcp.NewToolResultText(string(resultJSON)), nil
}

func toolResultError(message string) (*mcp.CallToolResult, error) {
	return mcp.NewToolResultError(message), nil
}

func toolboxAPIError(prefix string, err error) (*mcp.CallToolResult, error) {
	if err == nil {
		return nil, nil
	}

	msg := err.Error()

	var apiErr interface{ Body() []byte }
	if errors.As(err, &apiErr) {
		body := strings.TrimSpace(string(apiErr.Body()))
		if body != "" {
			msg = fmt.Sprintf("%s: %s", msg, body)
		}
	}

	return toolResultError(fmt.Sprintf("%s: %s", prefix, msg))
}

func screenshotMimeType(format string) string {
	switch strings.ToLower(strings.TrimSpace(format)) {
	case "jpeg", "jpg":
		return "image/jpeg"
	default:
		return "image/png"
	}
}
