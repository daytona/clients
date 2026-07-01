// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package mcp

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	mcpserver "github.com/mark3labs/mcp-go/server"
)

type toolListResponse struct {
	ID     int              `json:"id"`
	Error  *json.RawMessage `json:"error,omitempty"`
	Result struct {
		Tools []toolDefinition `json:"tools"`
	} `json:"result"`
}

type toolDefinition struct {
	Name        string `json:"name"`
	InputSchema struct {
		Required []string `json:"required"`
	} `json:"inputSchema"`
}

func TestNewDaytonaMCPServerListsComputerUseToolsOverStdio(t *testing.T) {
	expected := map[string]struct{}{
		"computer_use_start":                        {},
		"computer_use_stop":                         {},
		"computer_use_status":                       {},
		"computer_use_screenshot":                   {},
		"computer_use_screenshot_region":            {},
		"computer_use_screenshot_compressed":        {},
		"computer_use_screenshot_compressed_region": {},
		"computer_use_mouse_position":               {},
		"computer_use_mouse_move":                   {},
		"computer_use_mouse_click":                  {},
		"computer_use_mouse_drag":                   {},
		"computer_use_mouse_scroll":                 {},
		"computer_use_keyboard_type":                {},
		"computer_use_keyboard_press":               {},
		"computer_use_keyboard_hotkey":              {},
		"computer_use_display_info":                 {},
		"computer_use_windows":                      {},
		"computer_use_recording_start":              {},
		"computer_use_recording_stop":               {},
		"computer_use_recording_list":               {},
		"computer_use_recording_get":                {},
		"computer_use_recording_delete":             {},
		"computer_use_accessibility_tree":           {},
		"computer_use_accessibility_find":           {},
		"computer_use_accessibility_focus":          {},
		"computer_use_accessibility_invoke":         {},
		"computer_use_accessibility_set_value":      {},
	}

	input := strings.Join([]string{
		`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"stdio-test","version":"test"}}}`,
		`{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}`,
		`{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}`,
	}, "\n") + "\n"

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	srv := NewDaytonaMCPServer()
	stdio := mcpserver.NewStdioServer(&srv.MCPServer)
	var out strings.Builder
	if err := stdio.Listen(ctx, strings.NewReader(input), &out); err != nil {
		t.Fatalf("stdio Listen() error = %v", err)
	}

	list := parseToolsListResponse(t, out.String())
	if list.Error != nil {
		t.Fatalf("tools/list returned JSON-RPC error: %s", string(*list.Error))
	}

	toolsByName := make(map[string]toolDefinition, len(list.Result.Tools))
	computerUseTools := make(map[string]struct{})
	for _, tool := range list.Result.Tools {
		toolsByName[tool.Name] = tool
		if strings.HasPrefix(tool.Name, "computer_use_") {
			computerUseTools[tool.Name] = struct{}{}
		}
	}

	for name := range expected {
		if _, ok := computerUseTools[name]; !ok {
			t.Fatalf("missing computer-use tool %q", name)
		}
	}
	for name := range computerUseTools {
		if _, ok := expected[name]; !ok {
			t.Fatalf("unexpected computer-use tool %q", name)
		}
	}
	if len(computerUseTools) != len(expected) {
		t.Fatalf("expected %d computer-use tools, got %d", len(expected), len(computerUseTools))
	}
	if _, ok := toolsByName["computer_use_recording_download"]; ok {
		t.Fatal("computer_use_recording_download must not be registered")
	}

	assertRequired(t, toolsByName, "computer_use_screenshot", "id")
	assertRequired(t, toolsByName, "computer_use_mouse_move", "id", "x", "y")
	assertRequired(t, toolsByName, "computer_use_recording_stop", "id", "recording_id")
	assertRequired(t, toolsByName, "computer_use_accessibility_set_value", "id", "node_id", "value")
}

func parseToolsListResponse(t *testing.T, output string) toolListResponse {
	t.Helper()

	for _, line := range strings.Split(output, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}

		var response toolListResponse
		if err := json.Unmarshal([]byte(line), &response); err != nil {
			t.Fatalf("failed to parse JSON-RPC response %q: %v", line, err)
		}
		if response.ID == 2 {
			return response
		}
	}

	t.Fatalf("tools/list response was missing from output: %s", output)
	return toolListResponse{}
}

func assertRequired(t *testing.T, toolsByName map[string]toolDefinition, name string, required ...string) {
	t.Helper()

	tool, ok := toolsByName[name]
	if !ok {
		t.Fatalf("tool %q was missing", name)
	}

	seen := make(map[string]struct{}, len(tool.InputSchema.Required))
	for _, field := range tool.InputSchema.Required {
		seen[field] = struct{}{}
	}
	for _, field := range required {
		if _, ok := seen[field]; !ok {
			t.Fatalf("tool %q missing required field %q; required=%v", name, field, tool.InputSchema.Required)
		}
	}
}
