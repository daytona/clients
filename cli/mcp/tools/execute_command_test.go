// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/mark3labs/mcp-go/mcp"
)

func assertTextErrorResult(t *testing.T, result *mcp.CallToolResult, wantText, wantType string) {
	t.Helper()

	raw, err := json.Marshal(result)
	if err != nil {
		t.Fatalf("marshal result: %v", err)
	}
	var wire struct {
		IsError bool `json:"isError"`
		Content []struct {
			Type string `json:"type"`
			Text string `json:"text"`
		} `json:"content"`
		Meta map[string]any `json:"_meta"`
	}
	if err := json.Unmarshal(raw, &wire); err != nil {
		t.Fatalf("unmarshal result %s: %v", raw, err)
	}

	if !wire.IsError {
		t.Errorf("isError = false, want true (%s)", raw)
	}
	if wire.Content == nil {
		t.Fatalf("content is null, want an array (%s)", raw)
	}
	if len(wire.Content) != 1 || wire.Content[0].Type != "text" || wire.Content[0].Text != wantText {
		t.Errorf("content = %+v, want one text item %q", wire.Content, wantText)
	}
	if wire.Meta["Stderr"] != wantText || wire.Meta["ErrorType"] != wantType {
		t.Errorf("_meta = %v, want Stderr %q and ErrorType %q", wire.Meta, wantText, wantType)
	}
}

func TestExecuteCommandValidationErrorsHaveTextContent(t *testing.T) {
	empty := ""
	command := "echo hi"
	sandboxID := "sandbox-1"

	tests := []struct {
		name     string
		args     ExecuteCommandArgs
		wantText string
		wantType string
	}{
		{"missing id", ExecuteCommandArgs{Command: &command}, "Sandbox ID is required", "SandboxError"},
		{"empty id", ExecuteCommandArgs{Id: &empty, Command: &command}, "Sandbox ID is required", "SandboxError"},
		{"missing command", ExecuteCommandArgs{Id: &sandboxID}, "Command must be a non-empty string", "ValueError"},
		{"empty command", ExecuteCommandArgs{Id: &sandboxID, Command: &empty}, "Command must be a non-empty string", "ValueError"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result, err := ExecuteCommand(context.Background(), mcp.CallToolRequest{}, tt.args)
			if err != nil {
				t.Fatalf("ExecuteCommand returned error: %v", err)
			}
			assertTextErrorResult(t, result, tt.wantText, tt.wantType)
		})
	}
}

func TestReturnCommandErrorHasTextContent(t *testing.T) {
	result, err := returnCommandError("Command execution failed: boom", "CommandExecutionError")
	if err != nil {
		t.Fatalf("returnCommandError returned error: %v", err)
	}
	assertTextErrorResult(t, result, "Command execution failed: boom", "CommandExecutionError")
}
