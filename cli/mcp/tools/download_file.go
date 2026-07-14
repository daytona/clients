// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"path/filepath"

	"github.com/mark3labs/mcp-go/mcp"
)

type FileDownloadArgs struct {
	Id       *string `json:"id,omitempty"`
	FilePath *string `json:"filePath,omitempty"`
}

type Content struct {
	Type string `json:"type"`
	Text string `json:"text,omitempty"`
	Data string `json:"data,omitempty"`
}

func GetFileDownloadTool() mcp.Tool {
	return mcp.NewTool("file_download",
		mcp.WithDescription("Download a file from the Daytona sandbox. Returns the file content either as text or as a base64 encoded image. Handles special cases like matplotlib plots stored as JSON with embedded base64 images."),
		mcp.WithString("filePath", mcp.Required(), mcp.Description("Path to the file to download.")),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox to download the file from.")),
	)
}

func FileDownload(ctx context.Context, request mcp.CallToolRequest, args FileDownloadArgs) (*mcp.CallToolResult, error) {
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

	// Download the file
	file, _, apiErr := toolboxClient.FileSystemAPI.DownloadFile(ctx).Path(*args.FilePath).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to download file", apiErr)
	}
	defer file.Close()

	// Read file content
	content, err := io.ReadAll(file)
	if err != nil {
		return toolResultError(fmt.Sprintf("error reading file content: %v", err))
	}

	// Process file content based on file type
	ext := filepath.Ext(*args.FilePath)
	var result []Content

	switch ext {
	case ".png", ".jpg", ".jpeg", ".gif":
		// For image files, return as base64 encoded data
		result = []Content{{
			Type: "image",
			Data: string(content),
		}}
	case ".json":
		// For JSON files, try to parse and handle special cases like matplotlib plots
		var jsonData map[string]interface{}
		if err := json.Unmarshal(content, &jsonData); err != nil {
			// If not valid JSON, return as text
			result = []Content{{
				Type: "text",
				Text: string(content),
			}}
		} else {
			// Check if it's a matplotlib plot
			if _, ok := jsonData["data"]; ok {
				result = []Content{{
					Type: "image",
					Data: jsonData["data"].(string),
				}}
			} else {
				result = []Content{{
					Type: "text",
					Text: string(content),
				}}
			}
		}
	default:
		// For all other files, return as text
		result = []Content{{
			Type: "text",
			Text: string(content),
		}}
	}

	// Convert result to JSON
	resultJSON, err := json.Marshal(result)
	if err != nil {
		return toolResultError(fmt.Sprintf("error marshaling result: %v", err))
	}

	return mcp.NewToolResultText(string(resultJSON)), nil
}
