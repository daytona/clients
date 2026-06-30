// Copyright 2025 Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import (
	"context"

	toolboxclient "github.com/daytona/clients/toolbox-api-client-go"
	"github.com/mark3labs/mcp-go/mcp"
)

type ComputerUseRecordingStartArgs struct {
	Id    *string `json:"id,omitempty"`
	Label *string `json:"label,omitempty"`
}

type ComputerUseRecordingStopArgs struct {
	Id          *string `json:"id,omitempty"`
	RecordingId *string `json:"recording_id,omitempty"`
}

type ComputerUseRecordingGetArgs struct {
	Id          *string `json:"id,omitempty"`
	RecordingId *string `json:"recording_id,omitempty"`
}

type ComputerUseRecordingDeleteArgs struct {
	Id          *string `json:"id,omitempty"`
	RecordingId *string `json:"recording_id,omitempty"`
}

type recordingMetadata struct {
	DurationSeconds *float32 `json:"durationSeconds,omitempty"`
	EndTime         *string  `json:"endTime,omitempty"`
	FileName        string   `json:"fileName"`
	Id              string   `json:"id"`
	SizeBytes       *int32   `json:"sizeBytes,omitempty"`
	StartTime       string   `json:"startTime"`
	Status          string   `json:"status"`
}

func recordingMetadataFrom(rec *toolboxclient.Recording) recordingMetadata {
	if rec == nil {
		return recordingMetadata{}
	}
	return recordingMetadata{
		DurationSeconds: rec.DurationSeconds,
		EndTime:         rec.EndTime,
		FileName:        rec.FileName,
		Id:              rec.Id,
		SizeBytes:       rec.SizeBytes,
		StartTime:       rec.StartTime,
		Status:          rec.Status,
	}
}

func toolResultRecording(rec *toolboxclient.Recording) (*mcp.CallToolResult, error) {
	return toolResultJSON(recordingMetadataFrom(rec))
}

func toolResultRecordingList(resp *toolboxclient.ListRecordingsResponse) (*mcp.CallToolResult, error) {
	recordings := []recordingMetadata{}
	if resp != nil {
		recordings = make([]recordingMetadata, 0, len(resp.Recordings))
		for i := range resp.Recordings {
			recordings = append(recordings, recordingMetadataFrom(&resp.Recordings[i]))
		}
	}
	return toolResultJSON(map[string]any{"recordings": recordings})
}

func GetComputerUseRecordingStartTool() mcp.Tool {
	return mcp.NewTool("computer_use_recording_start",
		mcp.WithDescription("Start a screen recording in the Daytona sandbox desktop environment."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithString("label", mcp.Description("Optional label for the recording.")),
	)
}

func ComputerUseRecordingStart(ctx context.Context, request mcp.CallToolRequest, args ComputerUseRecordingStartArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	req := toolboxclient.NewStartRecordingRequest()
	if args.Label != nil {
		req.SetLabel(*args.Label)
	}

	result, _, apiErr := toolboxClient.ComputerUseAPI.StartRecording(ctx).Request(*req).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to start recording", apiErr)
	}

	return toolResultRecording(result)
}

func GetComputerUseRecordingStopTool() mcp.Tool {
	return mcp.NewTool("computer_use_recording_stop",
		mcp.WithDescription("Stop an active screen recording in the Daytona sandbox desktop environment."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithString("recording_id", mcp.Required(), mcp.Description("ID of the recording to stop.")),
	)
}

func ComputerUseRecordingStop(ctx context.Context, request mcp.CallToolRequest, args ComputerUseRecordingStopArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}
	if args.RecordingId == nil || *args.RecordingId == "" {
		return toolResultError("recording_id is required")
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	req := toolboxclient.NewStopRecordingRequest(*args.RecordingId)
	result, _, apiErr := toolboxClient.ComputerUseAPI.StopRecording(ctx).Request(*req).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to stop recording", apiErr)
	}

	return toolResultRecording(result)
}

func GetComputerUseRecordingListTool() mcp.Tool {
	return mcp.NewTool("computer_use_recording_list",
		mcp.WithDescription("List screen recordings in the Daytona sandbox desktop environment."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
	)
}

func ComputerUseRecordingList(ctx context.Context, request mcp.CallToolRequest, args SandboxIdArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	result, _, apiErr := toolboxClient.ComputerUseAPI.ListRecordings(ctx).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to list recordings", apiErr)
	}

	return toolResultRecordingList(result)
}

func GetComputerUseRecordingGetTool() mcp.Tool {
	return mcp.NewTool("computer_use_recording_get",
		mcp.WithDescription("Get metadata for a screen recording in the Daytona sandbox desktop environment."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithString("recording_id", mcp.Required(), mcp.Description("ID of the recording.")),
	)
}

func ComputerUseRecordingGet(ctx context.Context, request mcp.CallToolRequest, args ComputerUseRecordingGetArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}
	if args.RecordingId == nil || *args.RecordingId == "" {
		return toolResultError("recording_id is required")
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	result, _, apiErr := toolboxClient.ComputerUseAPI.GetRecording(ctx, *args.RecordingId).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to get recording", apiErr)
	}

	return toolResultRecording(result)
}

func GetComputerUseRecordingDeleteTool() mcp.Tool {
	return mcp.NewTool("computer_use_recording_delete",
		mcp.WithDescription("Delete a screen recording in the Daytona sandbox desktop environment."),
		mcp.WithString("id", mcp.Required(), mcp.Description("ID of the sandbox.")),
		mcp.WithString("recording_id", mcp.Required(), mcp.Description("ID of the recording to delete.")),
	)
}

func ComputerUseRecordingDelete(ctx context.Context, request mcp.CallToolRequest, args ComputerUseRecordingDeleteArgs) (*mcp.CallToolResult, error) {
	sandboxID, errResult, err := requireSandboxID(args.Id)
	if errResult != nil || err != nil {
		return errResult, err
	}
	if args.RecordingId == nil || *args.RecordingId == "" {
		return toolResultError("recording_id is required")
	}

	toolboxClient, errResult, err := getSandboxAndToolboxClient(ctx, sandboxID, true)
	if errResult != nil || err != nil {
		return errResult, err
	}

	_, apiErr := toolboxClient.ComputerUseAPI.DeleteRecording(ctx, *args.RecordingId).Execute()
	if apiErr != nil {
		return toolboxAPIError("Failed to delete recording", apiErr)
	}

	return mcp.NewToolResultText("Recording deleted successfully"), nil
}
