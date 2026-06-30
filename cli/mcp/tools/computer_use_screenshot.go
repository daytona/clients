// Copyright 2025 Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import (
	"context"
	"fmt"

	toolboxclient "github.com/daytona/clients/toolbox-api-client-go"
	"github.com/mark3labs/mcp-go/mcp"
)

type ComputerUseScreenshotArgs struct {
	Id         *string `json:"id,omitempty"`
	ShowCursor *bool   `json:"show_cursor,omitempty"`
}

type ComputerUseScreenshotRegionArgs struct {
	Id         *string `json:"id,omitempty"`
	X          *int    `json:"x,omitempty"`
	Y          *int    `json:"y,omitempty"`
	Width      *int    `json:"width,omitempty"`
	Height     *int    `json:"height,omitempty"`
	ShowCursor *bool   `json:"show_cursor,omitempty"`
}

type ComputerUseScreenshotCompressedArgs struct {
	Id         *string  `json:"id,omitempty"`
	ShowCursor *bool    `json:"show_cursor,omitempty"`
	Format     *string  `json:"format,omitempty"`
	Quality    *int     `json:"quality,omitempty"`
	Scale      *float64 `json:"scale,omitempty"`
}

type ComputerUseScreenshotCompressedRegionArgs struct {
	Id         *string  `json:"id,omitempty"`
	X          *int     `json:"x,omitempty"`
	Y          *int     `json:"y,omitempty"`
	Width      *int     `json:"width,omitempty"`
	Height     *int     `json:"height,omitempty"`
	ShowCursor *bool    `json:"show_cursor,omitempty"`
	Format     *string  `json:"format,omitempty"`
	Quality    *int     `json:"quality,omitempty"`
	Scale      *float64 `json:"scale,omitempty"`
}

func GetComputerUseScreenshotTool() mcp.Tool {
	return mcp.NewTool("computer_use_screenshot",
		mcp.WithDescription("Take a full-screen screenshot of the Daytona sandbox desktop. Returns MCP image content. Call computer_use_start first."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithBoolean("show_cursor", mcp.Description("Include the mouse cursor in the screenshot.")),
	)
}

func ComputerUseScreenshot(ctx context.Context, request mcp.CallToolRequest, args ComputerUseScreenshotArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	req := toolboxClient.ComputerUseAPI.TakeScreenshot(ctx)
	if args.ShowCursor != nil {
		req = req.ShowCursor(*args.ShowCursor)
	}

	result, _, apiErr := req.Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to take screenshot", apiErr)
	}

	return screenshotToolResult(result, "image/png")
}

func GetComputerUseScreenshotRegionTool() mcp.Tool {
	return mcp.NewTool("computer_use_screenshot_region",
		mcp.WithDescription("Take a region screenshot of the Daytona sandbox desktop. Returns MCP image content."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithNumber("x", mcp.Required(), mcp.Description("Left coordinate of the region.")),
		mcp.WithNumber("y", mcp.Required(), mcp.Description("Top coordinate of the region.")),
		mcp.WithNumber("width", mcp.Required(), mcp.Description("Width of the region in pixels.")),
		mcp.WithNumber("height", mcp.Required(), mcp.Description("Height of the region in pixels.")),
		mcp.WithBoolean("show_cursor", mcp.Description("Include the mouse cursor in the screenshot.")),
	)
}

func ComputerUseScreenshotRegion(ctx context.Context, request mcp.CallToolRequest, args ComputerUseScreenshotRegionArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}
	if args.X == nil || args.Y == nil || args.Width == nil || args.Height == nil {
		return toolResultError("x, y, width, and height are required")
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	req := toolboxClient.ComputerUseAPI.TakeRegionScreenshot(ctx).
		X(int32(*args.X)).
		Y(int32(*args.Y)).
		Width(int32(*args.Width)).
		Height(int32(*args.Height))
	if args.ShowCursor != nil {
		req = req.ShowCursor(*args.ShowCursor)
	}

	result, _, apiErr := req.Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to take region screenshot", apiErr)
	}

	return screenshotToolResult(result, "image/png")
}

func GetComputerUseScreenshotCompressedTool() mcp.Tool {
	return mcp.NewTool("computer_use_screenshot_compressed",
		mcp.WithDescription("Take a compressed full-screen screenshot of the Daytona sandbox desktop. Returns MCP image content."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithBoolean("show_cursor", mcp.Description("Include the mouse cursor in the screenshot.")),
		mcp.WithString("format", mcp.Description("Image format: png or jpeg.")),
		mcp.WithNumber("quality", mcp.Description("Compression quality (1-100) for jpeg.")),
		mcp.WithNumber("scale", mcp.Description("Scale factor for the screenshot (e.g. 0.5).")),
	)
}

func ComputerUseScreenshotCompressed(ctx context.Context, request mcp.CallToolRequest, args ComputerUseScreenshotCompressedArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	req := toolboxClient.ComputerUseAPI.TakeCompressedScreenshot(ctx)
	if args.ShowCursor != nil {
		req = req.ShowCursor(*args.ShowCursor)
	}
	if args.Format != nil {
		req = req.Format(*args.Format)
	}
	if args.Quality != nil {
		req = req.Quality(int32(*args.Quality))
	}
	if args.Scale != nil {
		req = req.Scale(float32(*args.Scale))
	}

	result, _, apiErr := req.Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to take compressed screenshot", apiErr)
	}

	format := ""
	if args.Format != nil {
		format = *args.Format
	}
	return screenshotToolResult(result, screenshotMimeType(format))
}

func GetComputerUseScreenshotCompressedRegionTool() mcp.Tool {
	return mcp.NewTool("computer_use_screenshot_compressed_region",
		mcp.WithDescription("Take a compressed region screenshot of the Daytona sandbox desktop. Returns MCP image content."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithNumber("x", mcp.Required(), mcp.Description("Left coordinate of the region.")),
		mcp.WithNumber("y", mcp.Required(), mcp.Description("Top coordinate of the region.")),
		mcp.WithNumber("width", mcp.Required(), mcp.Description("Width of the region in pixels.")),
		mcp.WithNumber("height", mcp.Required(), mcp.Description("Height of the region in pixels.")),
		mcp.WithBoolean("show_cursor", mcp.Description("Include the mouse cursor in the screenshot.")),
		mcp.WithString("format", mcp.Description("Image format: png or jpeg.")),
		mcp.WithNumber("quality", mcp.Description("Compression quality (1-100) for jpeg.")),
		mcp.WithNumber("scale", mcp.Description("Scale factor for the screenshot (e.g. 0.5).")),
	)
}

func ComputerUseScreenshotCompressedRegion(ctx context.Context, request mcp.CallToolRequest, args ComputerUseScreenshotCompressedRegionArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}
	if args.X == nil || args.Y == nil || args.Width == nil || args.Height == nil {
		return toolResultError("x, y, width, and height are required")
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	req := toolboxClient.ComputerUseAPI.TakeCompressedRegionScreenshot(ctx).
		X(int32(*args.X)).
		Y(int32(*args.Y)).
		Width(int32(*args.Width)).
		Height(int32(*args.Height))
	if args.ShowCursor != nil {
		req = req.ShowCursor(*args.ShowCursor)
	}
	if args.Format != nil {
		req = req.Format(*args.Format)
	}
	if args.Quality != nil {
		req = req.Quality(int32(*args.Quality))
	}
	if args.Scale != nil {
		req = req.Scale(float32(*args.Scale))
	}

	result, _, apiErr := req.Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to take compressed region screenshot", apiErr)
	}

	format := ""
	if args.Format != nil {
		format = *args.Format
	}
	return screenshotToolResult(result, screenshotMimeType(format))
}

func screenshotToolResult(result *toolboxclient.ScreenshotResponse, mimeType string) (*mcp.CallToolResult, error) {
	if result == nil {
		return toolResultError("Screenshot response was empty")
	}

	screenshot, ok := result.GetScreenshotOk()
	if !ok || screenshot == nil || *screenshot == "" {
		return toolResultError("Screenshot response did not include image data")
	}

	summary := "Sandbox desktop screenshot"
	if cursor, ok := result.GetCursorPositionOk(); ok && cursor != nil {
		summary = fmt.Sprintf("Sandbox desktop screenshot (cursor: x=%d, y=%d, sizeBytes=%d)",
			cursor.GetX(), cursor.GetY(), result.GetSizeBytes())
	}

	return mcp.NewToolResultImage(summary, *screenshot, mimeType), nil
}
