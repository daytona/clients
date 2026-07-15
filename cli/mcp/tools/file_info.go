// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/mark3labs/mcp-go/mcp"

	log "github.com/sirupsen/logrus"
)

type FileInfoArgs struct {
	Id       *string `json:"id,omitempty"`
	FilePath *string `json:"filePath,omitempty"`
}

func GetFileInfoTool() mcp.Tool {
	return mcp.NewTool("get_file_info",
		mcp.WithDescription("Get information about a file in the Daytona sandbox."),
		mcp.WithString("filePath", mcp.Required(), mcp.Description("Path to the file to get information about.")),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox to get the file information from.")),
	)
}

func FileInfo(ctx context.Context, request mcp.CallToolRequest, args FileInfoArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}

	if args.FilePath == nil || *args.FilePath == "" {
		return toolResultError("filePath parameter is required")
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	fileInfo, _, apiErr := toolboxClient.FileSystemAPI.GetFileInfo(ctx).Path(*args.FilePath).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to get file info", apiErr)
	}

	// Convert file info to JSON
	fileInfoJSON, err := json.MarshalIndent(fileInfo, "", "  ")
	if err != nil {
		return toolResultError(fmt.Sprintf("error marshaling file info: %v", err))
	}

	log.Infof("Retrieved file info for: %s", *args.FilePath)

	return mcp.NewToolResultText(string(fileInfoJSON)), nil
}
