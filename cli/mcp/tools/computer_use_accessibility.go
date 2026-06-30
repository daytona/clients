// Copyright 2025 Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import (
	"context"

	toolboxclient "github.com/daytona/clients/toolbox-api-client-go"
	"github.com/mark3labs/mcp-go/mcp"
)

type ComputerUseAccessibilityTreeArgs struct {
	Id       *string `json:"id,omitempty"`
	Scope    *string `json:"scope,omitempty"`
	Pid      *int    `json:"pid,omitempty"`
	MaxDepth *int    `json:"max_depth,omitempty"`
}

type ComputerUseAccessibilityFindArgs struct {
	Id        *string  `json:"id,omitempty"`
	Scope     *string  `json:"scope,omitempty"`
	Pid       *int     `json:"pid,omitempty"`
	Role      *string  `json:"role,omitempty"`
	Name      *string  `json:"name,omitempty"`
	NameMatch *string  `json:"name_match,omitempty"`
	States    []string `json:"states,omitempty"`
	Limit     *int     `json:"limit,omitempty"`
}

type ComputerUseAccessibilityNodeArgs struct {
	Id     *string `json:"id,omitempty"`
	NodeId *string `json:"node_id,omitempty"`
}

type ComputerUseAccessibilityInvokeArgs struct {
	Id     *string `json:"id,omitempty"`
	NodeId *string `json:"node_id,omitempty"`
	Action *string `json:"action,omitempty"`
}

type ComputerUseAccessibilitySetValueArgs struct {
	Id     *string `json:"id,omitempty"`
	NodeId *string `json:"node_id,omitempty"`
	Value  *string `json:"value,omitempty"`
}

func GetComputerUseAccessibilityTreeTool() mcp.Tool {
	return mcp.NewTool("computer_use_accessibility_tree",
		mcp.WithDescription("Fetch the AT-SPI accessibility tree from the Daytona sandbox desktop."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithString("scope", mcp.Description("Tree scope: focused, pid, or all.")),
		mcp.WithNumber("pid", mcp.Description("Process ID when scope is pid.")),
		mcp.WithNumber("max_depth", mcp.Description("Maximum tree depth to fetch.")),
	)
}

func ComputerUseAccessibilityTree(ctx context.Context, request mcp.CallToolRequest, args ComputerUseAccessibilityTreeArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	req := toolboxClient.ComputerUseAPI.GetAccessibilityTree(ctx)
	if args.Scope != nil {
		req = req.Scope(*args.Scope)
	}
	if args.Pid != nil {
		req = req.Pid(int32(*args.Pid))
	}
	if args.MaxDepth != nil {
		req = req.MaxDepth(int32(*args.MaxDepth))
	}

	result, _, apiErr := req.Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to get accessibility tree", apiErr)
	}

	return toolResultJSON(result)
}

func GetComputerUseAccessibilityFindTool() mcp.Tool {
	return mcp.NewTool("computer_use_accessibility_find",
		mcp.WithDescription("Find accessibility nodes matching filters in the Daytona sandbox desktop."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithString("scope", mcp.Description("Search scope: focused, pid, or all.")),
		mcp.WithNumber("pid", mcp.Description("Process ID when scope is pid.")),
		mcp.WithString("role", mcp.Description("AT-SPI role to match.")),
		mcp.WithString("name", mcp.Description("Accessible name to match.")),
		mcp.WithString("name_match", mcp.Description("Name match mode: exact, substring, or regex.")),
		mcp.WithArray("states", mcp.Description("Accessibility states to match."), mcp.Items(map[string]any{"type": "string"})),
		mcp.WithNumber("limit", mcp.Description("Maximum number of nodes to return.")),
	)
}

func ComputerUseAccessibilityFind(ctx context.Context, request mcp.CallToolRequest, args ComputerUseAccessibilityFindArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	reqBody := toolboxclient.NewFindAccessibilityNodesRequest()
	if args.Scope != nil {
		reqBody.SetScope(*args.Scope)
	}
	if args.Pid != nil {
		reqBody.SetPid(int32(*args.Pid))
	}
	if args.Role != nil {
		reqBody.SetRole(*args.Role)
	}
	if args.Name != nil {
		reqBody.SetName(*args.Name)
	}
	if args.NameMatch != nil {
		reqBody.SetNameMatch(*args.NameMatch)
	}
	if len(args.States) > 0 {
		reqBody.SetStates(args.States)
	}
	if args.Limit != nil {
		reqBody.SetLimit(int32(*args.Limit))
	}

	result, _, apiErr := toolboxClient.ComputerUseAPI.FindAccessibilityNodes(ctx).Request(*reqBody).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to find accessibility nodes", apiErr)
	}

	return toolResultJSON(result)
}

func GetComputerUseAccessibilityFocusTool() mcp.Tool {
	return mcp.NewTool("computer_use_accessibility_focus",
		mcp.WithDescription("Move keyboard focus to an accessibility node in the Daytona sandbox desktop."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithString("node_id", mcp.Required(), mcp.Description("Accessibility node ID (bus-name:object-path).")),
	)
}

func ComputerUseAccessibilityFocus(ctx context.Context, request mcp.CallToolRequest, args ComputerUseAccessibilityNodeArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}
	if args.NodeId == nil || *args.NodeId == "" {
		return toolResultError("node_id is required")
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	req := toolboxclient.NewAccessibilityNodeRequest(*args.NodeId)
	result, _, apiErr := toolboxClient.ComputerUseAPI.FocusAccessibilityNode(ctx).Request(*req).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to focus accessibility node", apiErr)
	}

	return toolResultJSON(result)
}

func GetComputerUseAccessibilityInvokeTool() mcp.Tool {
	return mcp.NewTool("computer_use_accessibility_invoke",
		mcp.WithDescription("Invoke an action on an accessibility node in the Daytona sandbox desktop."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithString("node_id", mcp.Required(), mcp.Description("Accessibility node ID (bus-name:object-path).")),
		mcp.WithString("action", mcp.Description("Action to invoke (e.g. click). Defaults to click.")),
	)
}

func ComputerUseAccessibilityInvoke(ctx context.Context, request mcp.CallToolRequest, args ComputerUseAccessibilityInvokeArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}
	if args.NodeId == nil || *args.NodeId == "" {
		return toolResultError("node_id is required")
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	req := toolboxclient.NewAccessibilityInvokeRequest(*args.NodeId)
	if args.Action != nil {
		req.SetAction(*args.Action)
	} else {
		req.SetAction("click")
	}

	result, _, apiErr := toolboxClient.ComputerUseAPI.InvokeAccessibilityNode(ctx).Request(*req).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to invoke accessibility node", apiErr)
	}

	return toolResultJSON(result)
}

func GetComputerUseAccessibilitySetValueTool() mcp.Tool {
	return mcp.NewTool("computer_use_accessibility_set_value",
		mcp.WithDescription("Set the value of an accessibility node in the Daytona sandbox desktop."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithString("node_id", mcp.Required(), mcp.Description("Accessibility node ID (bus-name:object-path).")),
		mcp.WithString("value", mcp.Required(), mcp.Description("Value to set on the node.")),
	)
}

func ComputerUseAccessibilitySetValue(ctx context.Context, request mcp.CallToolRequest, args ComputerUseAccessibilitySetValueArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}
	if args.NodeId == nil || *args.NodeId == "" {
		return toolResultError("node_id is required")
	}
	if args.Value == nil {
		return toolResultError("value is required")
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	req := toolboxclient.NewAccessibilitySetValueRequest(*args.NodeId)
	req.SetValue(*args.Value)

	result, _, apiErr := toolboxClient.ComputerUseAPI.SetAccessibilityNodeValue(ctx).Request(*req).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to set accessibility node value", apiErr)
	}

	return toolResultJSON(result)
}
