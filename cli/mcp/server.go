// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package mcp

import (
	"github.com/daytona/clients/cli/mcp/tools"
	"github.com/mark3labs/mcp-go/mcp"
	"github.com/mark3labs/mcp-go/server"
)

type DaytonaMCPServer struct {
	server.MCPServer
}

func NewDaytonaMCPServer() *DaytonaMCPServer {
	s := &DaytonaMCPServer{}

	s.MCPServer = *server.NewMCPServer(
		"Daytona MCP Server",
		"0.0.0-dev",
		server.WithRecovery(),
		server.WithPromptCapabilities(false),
		server.WithResourceCapabilities(false, false),
		server.WithToolCapabilities(true),
		server.WithLogging(),
	)

	s.addTools()

	return s
}

func (s *DaytonaMCPServer) Start() error {
	return server.ServeStdio(&s.MCPServer)
}

func (s *DaytonaMCPServer) addTools() {
	s.AddTool(tools.GetCreateSandboxTool(), mcp.NewTypedToolHandler(tools.CreateSandbox))
	s.AddTool(tools.GetDestroySandboxTool(), mcp.NewTypedToolHandler(tools.DestroySandbox))

	s.AddTool(tools.GetFileUploadTool(), mcp.NewTypedToolHandler(tools.FileUpload))
	s.AddTool(tools.GetFileDownloadTool(), mcp.NewTypedToolHandler(tools.FileDownload))
	s.AddTool(tools.GetFileInfoTool(), mcp.NewTypedToolHandler(tools.FileInfo))
	s.AddTool(tools.GetListFilesTool(), mcp.NewTypedToolHandler(tools.ListFiles))
	s.AddTool(tools.GetMoveFileTool(), mcp.NewTypedToolHandler(tools.MoveFile))
	s.AddTool(tools.GetDeleteFileTool(), mcp.NewTypedToolHandler(tools.DeleteFile))
	s.AddTool(tools.GetCreateFolderTool(), mcp.NewTypedToolHandler(tools.CreateFolder))

	s.AddTool(tools.GetExecuteCommandTool(), mcp.NewTypedToolHandler(tools.ExecuteCommand))
	s.AddTool(tools.GetPreviewLinkTool(), mcp.NewTypedToolHandler(tools.PreviewLink))
	s.AddTool(tools.GetGitCloneTool(), mcp.NewTypedToolHandler(tools.GitClone))

	s.AddTool(tools.GetComputerUseStartTool(), mcp.NewTypedToolHandler(tools.ComputerUseStart))
	s.AddTool(tools.GetComputerUseStopTool(), mcp.NewTypedToolHandler(tools.ComputerUseStop))
	s.AddTool(tools.GetComputerUseStatusTool(), mcp.NewTypedToolHandler(tools.ComputerUseStatus))
	s.AddTool(tools.GetComputerUseScreenshotTool(), mcp.NewTypedToolHandler(tools.ComputerUseScreenshot))
	s.AddTool(tools.GetComputerUseScreenshotRegionTool(), mcp.NewTypedToolHandler(tools.ComputerUseScreenshotRegion))
	s.AddTool(tools.GetComputerUseScreenshotCompressedTool(), mcp.NewTypedToolHandler(tools.ComputerUseScreenshotCompressed))
	s.AddTool(tools.GetComputerUseScreenshotCompressedRegionTool(), mcp.NewTypedToolHandler(tools.ComputerUseScreenshotCompressedRegion))
	s.AddTool(tools.GetComputerUseMousePositionTool(), mcp.NewTypedToolHandler(tools.ComputerUseMousePosition))
	s.AddTool(tools.GetComputerUseMouseMoveTool(), mcp.NewTypedToolHandler(tools.ComputerUseMouseMove))
	s.AddTool(tools.GetComputerUseMouseClickTool(), mcp.NewTypedToolHandler(tools.ComputerUseMouseClick))
	s.AddTool(tools.GetComputerUseMouseDragTool(), mcp.NewTypedToolHandler(tools.ComputerUseMouseDrag))
	s.AddTool(tools.GetComputerUseMouseScrollTool(), mcp.NewTypedToolHandler(tools.ComputerUseMouseScroll))
	s.AddTool(tools.GetComputerUseKeyboardTypeTool(), mcp.NewTypedToolHandler(tools.ComputerUseKeyboardType))
	s.AddTool(tools.GetComputerUseKeyboardPressTool(), mcp.NewTypedToolHandler(tools.ComputerUseKeyboardPress))
	s.AddTool(tools.GetComputerUseKeyboardHotkeyTool(), mcp.NewTypedToolHandler(tools.ComputerUseKeyboardHotkey))
	s.AddTool(tools.GetComputerUseDisplayInfoTool(), mcp.NewTypedToolHandler(tools.ComputerUseDisplayInfo))
	s.AddTool(tools.GetComputerUseWindowsTool(), mcp.NewTypedToolHandler(tools.ComputerUseWindows))
	s.AddTool(tools.GetComputerUseRecordingStartTool(), mcp.NewTypedToolHandler(tools.ComputerUseRecordingStart))
	s.AddTool(tools.GetComputerUseRecordingStopTool(), mcp.NewTypedToolHandler(tools.ComputerUseRecordingStop))
	s.AddTool(tools.GetComputerUseRecordingListTool(), mcp.NewTypedToolHandler(tools.ComputerUseRecordingList))
	s.AddTool(tools.GetComputerUseRecordingGetTool(), mcp.NewTypedToolHandler(tools.ComputerUseRecordingGet))
	s.AddTool(tools.GetComputerUseRecordingDeleteTool(), mcp.NewTypedToolHandler(tools.ComputerUseRecordingDelete))
	s.AddTool(tools.GetComputerUseAccessibilityTreeTool(), mcp.NewTypedToolHandler(tools.ComputerUseAccessibilityTree))
	s.AddTool(tools.GetComputerUseAccessibilityFindTool(), mcp.NewTypedToolHandler(tools.ComputerUseAccessibilityFind))
	s.AddTool(tools.GetComputerUseAccessibilityFocusTool(), mcp.NewTypedToolHandler(tools.ComputerUseAccessibilityFocus))
	s.AddTool(tools.GetComputerUseAccessibilityInvokeTool(), mcp.NewTypedToolHandler(tools.ComputerUseAccessibilityInvoke))
	s.AddTool(tools.GetComputerUseAccessibilitySetValueTool(), mcp.NewTypedToolHandler(tools.ComputerUseAccessibilitySetValue))
}
