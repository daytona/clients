// Copyright 2025 Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import (
	"strings"
	"testing"

	toolboxclient "github.com/daytona/clients/toolbox-api-client-go"
	"github.com/mark3labs/mcp-go/mcp"
)

func TestScreenshotToolResultReturnsTextAndImageContent(t *testing.T) {
	resp := toolboxclient.NewScreenshotResponse()
	resp.SetScreenshot("iVBORw0KGgo=")
	resp.SetSizeBytes(12)
	cursor := toolboxclient.NewPosition()
	cursor.SetX(7)
	cursor.SetY(9)
	resp.SetCursorPosition(*cursor)

	result, err := screenshotToolResult(resp, "image/jpeg")
	if err != nil {
		t.Fatalf("screenshotToolResult() error = %v", err)
	}
	if result.IsError {
		t.Fatal("expected successful screenshot result")
	}
	if len(result.Content) != 2 {
		t.Fatalf("expected 2 content items, got %d", len(result.Content))
	}

	text, ok := mcp.AsTextContent(result.Content[0])
	if !ok {
		t.Fatalf("expected first content item to be text, got %#v", result.Content[0])
	}
	for _, want := range []string{"Sandbox desktop screenshot", "x=7", "y=9", "sizeBytes=12"} {
		if !strings.Contains(text.Text, want) {
			t.Fatalf("expected text content to contain %q, got %q", want, text.Text)
		}
	}

	image, ok := mcp.AsImageContent(result.Content[1])
	if !ok {
		t.Fatalf("expected second content item to be image, got %#v", result.Content[1])
	}
	if image.Type != "image" {
		t.Fatalf("expected image type, got %q", image.Type)
	}
	if image.Data != "iVBORw0KGgo=" {
		t.Fatalf("expected image data, got %q", image.Data)
	}
	if image.MIMEType != "image/jpeg" {
		t.Fatalf("expected image/jpeg, got %q", image.MIMEType)
	}
}

func TestScreenshotToolResultRejectsMissingImageData(t *testing.T) {
	tests := []struct {
		name string
		resp *toolboxclient.ScreenshotResponse
		want string
	}{
		{name: "nil response", resp: nil, want: "Screenshot response was empty"},
		{name: "empty response", resp: toolboxclient.NewScreenshotResponse(), want: "Screenshot response did not include image data"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result, err := screenshotToolResult(tt.resp, "image/png")
			if err != nil {
				t.Fatalf("screenshotToolResult() error = %v", err)
			}
			if !result.IsError {
				t.Fatal("expected error result")
			}
			if len(result.Content) != 1 {
				t.Fatalf("expected one text content item, got %d", len(result.Content))
			}

			text, ok := mcp.AsTextContent(result.Content[0])
			if !ok {
				t.Fatalf("expected text content, got %#v", result.Content[0])
			}
			if !strings.Contains(text.Text, tt.want) {
				t.Fatalf("expected text content to contain %q, got %q", tt.want, text.Text)
			}
		})
	}
}
