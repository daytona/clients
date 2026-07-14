// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import (
	"context"
	"fmt"

	"github.com/mark3labs/mcp-go/mcp"

	log "github.com/sirupsen/logrus"
)

type MoveFileArgs struct {
	Id         *string `json:"id,omitempty"`
	SourcePath *string `json:"sourcePath,omitempty"`
	DestPath   *string `json:"destPath,omitempty"`
}

func GetMoveFileTool() mcp.Tool {
	return mcp.NewTool("move_file",
		mcp.WithDescription("Move or rename a file in the Daytona sandbox."),
		mcp.WithString("sourcePath", mcp.Required(), mcp.Description("Source path of the file to move.")),
		mcp.WithString("destPath", mcp.Required(), mcp.Description("Destination path where to move the file.")),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox to move the file in.")),
	)
}

func MoveFile(ctx context.Context, request mcp.CallToolRequest, args MoveFileArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}

	// Get source and destination paths from request arguments
	if args.SourcePath == nil || *args.SourcePath == "" {
		return toolResultError("sourcePath parameter is required")
	}

	if args.DestPath == nil || *args.DestPath == "" {
		return toolResultError("destPath parameter is required")
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	if _, apiErr := toolboxClient.FileSystemAPI.MoveFile(ctx).Source(*args.SourcePath).Destination(*args.DestPath).Execute(); apiErr != nil {
		return toolboxAPIError("Failed to move file", apiErr)
	}

	log.Infof("Moved file from %s to %s", *args.SourcePath, *args.DestPath)

	return mcp.NewToolResultText(fmt.Sprintf("Moved file from %s to %s", *args.SourcePath, *args.DestPath)), nil
}
