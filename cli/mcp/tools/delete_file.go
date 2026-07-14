// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import (
	"context"
	"fmt"

	"github.com/mark3labs/mcp-go/mcp"

	log "github.com/sirupsen/logrus"
)

type DeleteFileArgs struct {
	Id       *string `json:"id,omitempty"`
	FilePath *string `json:"filePath,omitempty"`
}

func GetDeleteFileTool() mcp.Tool {
	return mcp.NewTool("delete_file",
		mcp.WithDescription("Delete a file or directory in the Daytona sandbox."),
		mcp.WithString("filePath", mcp.Required(), mcp.Description("Path to the file or directory to delete.")),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox to delete the file in.")),
	)
}

func DeleteFile(ctx context.Context, request mcp.CallToolRequest, args DeleteFileArgs) (*mcp.CallToolResult, error) {
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

	if _, apiErr := toolboxClient.FileSystemAPI.DeleteFile(ctx).Path(*args.FilePath).Recursive(true).Execute(); apiErr != nil {
		return toolboxAPIError("Failed to delete file", apiErr)
	}

	log.Infof("Deleted file: %s", *args.FilePath)

	return mcp.NewToolResultText(fmt.Sprintf("Deleted file: %s", *args.FilePath)), nil
}
