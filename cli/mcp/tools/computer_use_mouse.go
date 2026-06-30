// Copyright 2025 Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import (
	"context"

	toolboxclient "github.com/daytona/clients/toolbox-api-client-go"
	"github.com/mark3labs/mcp-go/mcp"
)

type ComputerUseMouseMoveArgs struct {
	Id *string `json:"id,omitempty"`
	X  *int    `json:"x,omitempty"`
	Y  *int    `json:"y,omitempty"`
}

type ComputerUseMouseClickArgs struct {
	Id     *string `json:"id,omitempty"`
	X      *int    `json:"x,omitempty"`
	Y      *int    `json:"y,omitempty"`
	Button *string `json:"button,omitempty"`
	Double *bool   `json:"double,omitempty"`
}

type ComputerUseMouseDragArgs struct {
	Id     *string `json:"id,omitempty"`
	StartX *int    `json:"start_x,omitempty"`
	StartY *int    `json:"start_y,omitempty"`
	EndX   *int    `json:"end_x,omitempty"`
	EndY   *int    `json:"end_y,omitempty"`
	Button *string `json:"button,omitempty"`
}

type ComputerUseMouseScrollArgs struct {
	Id        *string `json:"id,omitempty"`
	X         *int    `json:"x,omitempty"`
	Y         *int    `json:"y,omitempty"`
	Direction *string `json:"direction,omitempty"`
	Amount    *int    `json:"amount,omitempty"`
}

func GetComputerUseMousePositionTool() mcp.Tool {
	return mcp.NewTool("computer_use_mouse_position",
		mcp.WithDescription("Get the current mouse cursor position in the Daytona sandbox desktop."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
	)
}

func ComputerUseMousePosition(ctx context.Context, request mcp.CallToolRequest, args SandboxIdArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	result, _, apiErr := toolboxClient.ComputerUseAPI.GetMousePosition(ctx).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to get mouse position", apiErr)
	}

	return toolResultJSON(result)
}

func GetComputerUseMouseMoveTool() mcp.Tool {
	return mcp.NewTool("computer_use_mouse_move",
		mcp.WithDescription("Move the mouse cursor to coordinates in the Daytona sandbox desktop."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithNumber("x", mcp.Required(), mcp.Description("Target X coordinate.")),
		mcp.WithNumber("y", mcp.Required(), mcp.Description("Target Y coordinate.")),
	)
}

func ComputerUseMouseMove(ctx context.Context, request mcp.CallToolRequest, args ComputerUseMouseMoveArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}
	if args.X == nil || args.Y == nil {
		return toolResultError("x and y are required")
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	req := toolboxclient.NewMouseMoveRequest()
	req.SetX(int32(*args.X))
	req.SetY(int32(*args.Y))

	result, _, apiErr := toolboxClient.ComputerUseAPI.MoveMouse(ctx).Request(*req).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to move mouse", apiErr)
	}

	return toolResultJSON(result)
}

func GetComputerUseMouseClickTool() mcp.Tool {
	return mcp.NewTool("computer_use_mouse_click",
		mcp.WithDescription("Click the mouse at coordinates in the Daytona sandbox desktop."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithNumber("x", mcp.Required(), mcp.Description("X coordinate.")),
		mcp.WithNumber("y", mcp.Required(), mcp.Description("Y coordinate.")),
		mcp.WithString("button", mcp.Description("Mouse button: left, right, or middle. Defaults to left.")),
		mcp.WithBoolean("double", mcp.Description("Perform a double click.")),
	)
}

func ComputerUseMouseClick(ctx context.Context, request mcp.CallToolRequest, args ComputerUseMouseClickArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}
	if args.X == nil || args.Y == nil {
		return toolResultError("x and y are required")
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	req := toolboxclient.NewMouseClickRequest()
	req.SetX(int32(*args.X))
	req.SetY(int32(*args.Y))
	if args.Button != nil {
		req.SetButton(*args.Button)
	} else {
		req.SetButton("left")
	}
	if args.Double != nil {
		req.SetDouble(*args.Double)
	}

	result, _, apiErr := toolboxClient.ComputerUseAPI.Click(ctx).Request(*req).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to click mouse", apiErr)
	}

	return toolResultJSON(result)
}

func GetComputerUseMouseDragTool() mcp.Tool {
	return mcp.NewTool("computer_use_mouse_drag",
		mcp.WithDescription("Drag the mouse from start to end coordinates in the Daytona sandbox desktop."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithNumber("start_x", mcp.Required(), mcp.Description("Start X coordinate.")),
		mcp.WithNumber("start_y", mcp.Required(), mcp.Description("Start Y coordinate.")),
		mcp.WithNumber("end_x", mcp.Required(), mcp.Description("End X coordinate.")),
		mcp.WithNumber("end_y", mcp.Required(), mcp.Description("End Y coordinate.")),
		mcp.WithString("button", mcp.Description("Mouse button: left, right, or middle. Defaults to left.")),
	)
}

func ComputerUseMouseDrag(ctx context.Context, request mcp.CallToolRequest, args ComputerUseMouseDragArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}
	if args.StartX == nil || args.StartY == nil || args.EndX == nil || args.EndY == nil {
		return toolResultError("start_x, start_y, end_x, and end_y are required")
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	req := toolboxclient.NewMouseDragRequest()
	req.SetStartX(int32(*args.StartX))
	req.SetStartY(int32(*args.StartY))
	req.SetEndX(int32(*args.EndX))
	req.SetEndY(int32(*args.EndY))
	if args.Button != nil {
		req.SetButton(*args.Button)
	} else {
		req.SetButton("left")
	}

	result, _, apiErr := toolboxClient.ComputerUseAPI.Drag(ctx).Request(*req).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to drag mouse", apiErr)
	}

	return toolResultJSON(result)
}

func GetComputerUseMouseScrollTool() mcp.Tool {
	return mcp.NewTool("computer_use_mouse_scroll",
		mcp.WithDescription("Scroll the mouse wheel at coordinates in the Daytona sandbox desktop."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithNumber("x", mcp.Required(), mcp.Description("X coordinate.")),
		mcp.WithNumber("y", mcp.Required(), mcp.Description("Y coordinate.")),
		mcp.WithString("direction", mcp.Required(), mcp.Description("Scroll direction: up or down.")),
		mcp.WithNumber("amount", mcp.Description("Scroll amount. Defaults to 3.")),
	)
}

func ComputerUseMouseScroll(ctx context.Context, request mcp.CallToolRequest, args ComputerUseMouseScrollArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}
	if args.X == nil || args.Y == nil || args.Direction == nil {
		return toolResultError("x, y, and direction are required")
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	req := toolboxclient.NewMouseScrollRequest()
	req.SetX(int32(*args.X))
	req.SetY(int32(*args.Y))
	req.SetDirection(*args.Direction)
	if args.Amount != nil {
		req.SetAmount(int32(*args.Amount))
	} else {
		req.SetAmount(3)
	}

	result, _, apiErr := toolboxClient.ComputerUseAPI.Scroll(ctx).Request(*req).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to scroll mouse", apiErr)
	}

	return toolResultJSON(result)
}
