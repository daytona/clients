// Copyright 2025 Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import (
	"context"

	"github.com/mark3labs/mcp-go/mcp"
)

func GetComputerUseDisplayInfoTool() mcp.Tool {
	return mcp.NewTool("computer_use_display_info",
		mcp.WithDescription("Get display information for the Daytona sandbox desktop environment."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
	)
}

func ComputerUseDisplayInfo(ctx context.Context, request mcp.CallToolRequest, args SandboxIdArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	result, _, apiErr := toolboxClient.ComputerUseAPI.GetDisplayInfo(ctx).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to get display info", apiErr)
	}

	return toolResultJSON(result)
}

func GetComputerUseWindowsTool() mcp.Tool {
	return mcp.NewTool("computer_use_windows",
		mcp.WithDescription("List open windows in the Daytona sandbox desktop environment."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
	)
}

func ComputerUseWindows(ctx context.Context, request mcp.CallToolRequest, args SandboxIdArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	result, _, apiErr := toolboxClient.ComputerUseAPI.GetWindows(ctx).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to get windows", apiErr)
	}

	return toolResultJSON(result)
}
