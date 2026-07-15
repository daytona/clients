// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import (
	"context"
	"fmt"

	"github.com/mark3labs/mcp-go/mcp"

	log "github.com/sirupsen/logrus"
)

type CreateFolderArgs struct {
	Id         *string `json:"id,omitempty"`
	FolderPath *string `json:"folderPath,omitempty"`
	Mode       *string `json:"mode,omitempty"`
}

func GetCreateFolderTool() mcp.Tool {
	return mcp.NewTool("create_folder",
		mcp.WithDescription("Create a new folder in the Daytona sandbox."),
		mcp.WithString("folderPath", mcp.Required(), mcp.Description("Path to the folder to create.")),
		mcp.WithString("mode", mcp.Description("Mode of the folder to create (defaults to 0755).")),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox to create the folder in.")),
	)
}

func CreateFolder(ctx context.Context, request mcp.CallToolRequest, args CreateFolderArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}

	if args.FolderPath == nil || *args.FolderPath == "" {
		return toolResultError("folderPath parameter is required")
	}

	mode := "0755" // default mode
	if args.Mode == nil || *args.Mode == "" {
		args.Mode = &mode
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	if _, apiErr := toolboxClient.FileSystemAPI.CreateFolder(ctx).Path(*args.FolderPath).Mode(*args.Mode).Execute(); apiErr != nil {
		return toolboxAPIError("Failed to create folder", apiErr)
	}

	log.Infof("Created folder: %s", *args.FolderPath)

	return mcp.NewToolResultText(fmt.Sprintf("Created folder: %s", *args.FolderPath)), nil
}
