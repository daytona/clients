// Copyright 2025 Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import (
	"context"

	toolboxclient "github.com/daytona/clients/toolbox-api-client-go"
	"github.com/mark3labs/mcp-go/mcp"
)

type ComputerUseKeyboardTypeArgs struct {
	Id    *string `json:"id,omitempty"`
	Text  *string `json:"text,omitempty"`
	Delay *int    `json:"delay,omitempty"`
}

type ComputerUseKeyboardPressArgs struct {
	Id        *string  `json:"id,omitempty"`
	Key       *string  `json:"key,omitempty"`
	Modifiers []string `json:"modifiers,omitempty"`
}

type ComputerUseKeyboardHotkeyArgs struct {
	Id   *string `json:"id,omitempty"`
	Keys *string `json:"keys,omitempty"`
}

func GetComputerUseKeyboardTypeTool() mcp.Tool {
	return mcp.NewTool("computer_use_keyboard_type",
		mcp.WithDescription("Type text using the keyboard in the Daytona sandbox desktop."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithString("text", mcp.Required(), mcp.Description("Text to type.")),
		mcp.WithNumber("delay", mcp.Description("Delay in milliseconds between keystrokes.")),
	)
}

func ComputerUseKeyboardType(ctx context.Context, request mcp.CallToolRequest, args ComputerUseKeyboardTypeArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}
	if args.Text == nil || *args.Text == "" {
		return toolResultError("text is required")
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	req := toolboxclient.NewKeyboardTypeRequest()
	req.SetText(*args.Text)
	if args.Delay != nil {
		delay, errResult, err := int32FromIntNonNegative(*args.Delay, "delay")
		if errResult != nil || err != nil {
			return errResult, err
		}
		req.SetDelay(delay)
	}

	result, _, apiErr := toolboxClient.ComputerUseAPI.TypeText(ctx).Request(*req).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to type text", apiErr)
	}

	return toolResultJSON(result)
}

func GetComputerUseKeyboardPressTool() mcp.Tool {
	return mcp.NewTool("computer_use_keyboard_press",
		mcp.WithDescription("Press a single key with optional modifiers in the Daytona sandbox desktop."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithString("key", mcp.Required(), mcp.Description("Key to press (e.g. enter, tab, a).")),
		mcp.WithArray("modifiers", mcp.Description("Modifier keys: ctrl, alt, shift, cmd."), mcp.Items(map[string]any{"type": "string"})),
	)
}

func ComputerUseKeyboardPress(ctx context.Context, request mcp.CallToolRequest, args ComputerUseKeyboardPressArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}
	if args.Key == nil || *args.Key == "" {
		return toolResultError("key is required")
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	req := toolboxclient.NewKeyboardPressRequest()
	req.SetKey(*args.Key)
	if len(args.Modifiers) > 0 {
		req.SetModifiers(args.Modifiers)
	}

	result, _, apiErr := toolboxClient.ComputerUseAPI.PressKey(ctx).Request(*req).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to press key", apiErr)
	}

	return toolResultJSON(result)
}

func GetComputerUseKeyboardHotkeyTool() mcp.Tool {
	return mcp.NewTool("computer_use_keyboard_hotkey",
		mcp.WithDescription("Press a keyboard hotkey combination in the Daytona sandbox desktop (e.g. ctrl+c)."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithString("keys", mcp.Required(), mcp.Description("Hotkey combination (e.g. ctrl+c, alt+tab).")),
	)
}

func ComputerUseKeyboardHotkey(ctx context.Context, request mcp.CallToolRequest, args ComputerUseKeyboardHotkeyArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}
	if args.Keys == nil || *args.Keys == "" {
		return toolResultError("keys is required")
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	req := toolboxclient.NewKeyboardHotkeyRequest()
	req.SetKeys(*args.Keys)

	result, _, apiErr := toolboxClient.ComputerUseAPI.PressHotkey(ctx).Request(*req).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to press hotkey", apiErr)
	}

	return toolResultJSON(result)
}
