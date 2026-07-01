// Copyright 2025 Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import (
	"context"

	"github.com/mark3labs/mcp-go/mcp"
)

func GetComputerUseStartTool() mcp.Tool {
	return mcp.NewTool("computer_use_start",
		mcp.WithDescription("Start the computer-use desktop environment in a Daytona sandbox (Xvfb, window manager, VNC/noVNC). Call this before mouse, keyboard, or screenshot tools."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
	)
}

func ComputerUseStart(ctx context.Context, request mcp.CallToolRequest, args SandboxIdArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	result, _, apiErr := toolboxClient.ComputerUseAPI.StartComputerUse(ctx).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to start computer use", apiErr)
	}

	return toolResultJSON(result)
}

func GetComputerUseStopTool() mcp.Tool {
	return mcp.NewTool("computer_use_stop",
		mcp.WithDescription("Stop the computer-use desktop environment in a Daytona sandbox and release desktop resources."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
	)
}

func ComputerUseStop(ctx context.Context, request mcp.CallToolRequest, args SandboxIdArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	result, _, apiErr := toolboxClient.ComputerUseAPI.StopComputerUse(ctx).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to stop computer use", apiErr)
	}

	return toolResultJSON(result)
}

func GetComputerUseStatusTool() mcp.Tool {
	return mcp.NewTool("computer_use_status",
		mcp.WithDescription("Get the status of computer-use processes in a Daytona sandbox desktop environment."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
	)
}

func ComputerUseStatus(ctx context.Context, request mcp.CallToolRequest, args SandboxIdArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	result, _, apiErr := toolboxClient.ComputerUseAPI.GetComputerUseStatus(ctx).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to get computer use status", apiErr)
	}

	return toolResultJSON(result)
}
