// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"

	"github.com/daytona/clients/sdk-go/pkg/common"
	sdkerrors "github.com/daytona/clients/sdk-go/pkg/errors"
	toolbox "github.com/daytona/clients/toolbox-api-client-go"
	"github.com/gorilla/websocket"
)

// ProcessOutput is the collected stdout, stderr, and terminal result of a process.
// Use [ProcessHandle.Output] after [ProcessHandle.Wait] when supervising a process
// started with [ProcessService.Start]; [ProcessService.Run] returns these values directly.
type ProcessOutput struct {
	Stdout   string                         // Collected standard output.
	Stderr   string                         // Collected standard error.
	ExitCode *int32                         // Exit code, when the process exited normally.
	Signal   *string                        // Signal that terminated the process, when applicable.
	Reason   *toolbox.ProcessTerminalReason // Terminal reason reported by the daemon.
}

// ProcessHandle supervises one background process started with [ProcessService.Start]
// or reconnected by ID with [ProcessService.Get] or [ProcessService.Connect].
type ProcessHandle struct {
	processID string
	service   *ProcessService
}

func newProcessHandle(processID string, service *ProcessService) *ProcessHandle {
	return &ProcessHandle{processID: processID, service: service}
}

// ID returns the process ID used to reconnect with [ProcessService.Connect].
func (h *ProcessHandle) ID() string { return h.processID }

// Get fetches the process's current record. Use it to inspect state without waiting;
// use [ProcessHandle.Wait] when the caller needs the terminal result.
func (h *ProcessHandle) Get(ctx context.Context) (*toolbox.Process, error) {
	process, httpResp, err := h.service.toolboxClient.ProcessAPI.GetProcess(ctx, h.processID).Execute()
	if err != nil {
		return nil, sdkerrors.ConvertToolboxError(err, httpResp)
	}
	return process, nil
}

// Logs replays one page of retained logs beginning at cursor. Pass the returned
// page cursor to the next call; on CURSOR_EXPIRED, restart from the error's first
// available cursor. Use [ProcessHandle.StreamLogs] for replay followed by live logs.
func (h *ProcessHandle) Logs(ctx context.Context, cursor string, limit int, encoding string) (*toolbox.ProcessLogPage, error) {
	request := h.service.toolboxClient.ProcessAPI.ReadProcessLogs(ctx, h.processID)
	if cursor != "" {
		request = request.Cursor(cursor)
	}
	if limit > 0 {
		request = request.Limit(int32(limit))
	}
	if encoding != "" {
		request = request.Encoding(encoding)
	}
	page, httpResp, err := request.Execute()
	if err != nil {
		return nil, sdkerrors.ConvertToolboxError(err, httpResp)
	}
	return page, nil
}

// Stdin writes data to a process configured with piped or PTY stdin. Use
// [ProcessHandle.StdinEOF] when no more input will be sent.
func (h *ProcessHandle) Stdin(ctx context.Context, data string) error {
	request := toolbox.NewProcessStdinRequest()
	request.SetData(data)
	httpResp, err := h.service.toolboxClient.ProcessAPI.SendProcessStdin(ctx, h.processID).Request(*request).Execute()
	return sdkerrors.ConvertToolboxError(err, httpResp)
}

// StdinEOF closes a process's piped stdin without killing it. Use
// [ProcessHandle.Kill] when the process must be terminated instead.
func (h *ProcessHandle) StdinEOF(ctx context.Context) error {
	request := toolbox.NewProcessStdinRequest()
	request.SetEof(true)
	httpResp, err := h.service.toolboxClient.ProcessAPI.SendProcessStdin(ctx, h.processID).Request(*request).Execute()
	return sdkerrors.ConvertToolboxError(err, httpResp)
}

// Kill signals a running process, defaulting to SIGKILL. Use
// [ProcessHandle.StdinEOF] to request normal completion from stdin-driven commands.
func (h *ProcessHandle) Kill(ctx context.Context, signal ...string) error {
	selected := "SIGKILL"
	if len(signal) > 0 && strings.TrimSpace(signal[0]) != "" {
		selected = strings.TrimSpace(signal[0])
	}
	request := toolbox.NewKillProcessRequest()
	request.SetSignal(selected)
	httpResp, err := h.service.toolboxClient.ProcessAPI.SignalProcess(ctx, h.processID).Request(*request).Execute()
	return sdkerrors.ConvertToolboxError(err, httpResp)
}

// Resize changes the terminal dimensions of a PTY process. It is not supported
// for exec processes; use it with a process started with kind "pty".
func (h *ProcessHandle) Resize(ctx context.Context, cols, rows int) error {
	if cols <= 0 || rows <= 0 {
		return sdkerrors.NewDaytonaError("terminal dimensions must be positive", http.StatusBadRequest, nil)
	}
	request := toolbox.NewResizeProcessRequest(int32(cols), int32(rows))
	_, httpResp, err := h.service.toolboxClient.ProcessAPI.ResizeProcess(ctx, h.processID).Request(*request).Execute()
	return sdkerrors.ConvertToolboxError(err, httpResp)
}

// Wait blocks until the process terminates and returns its terminal result. It
// does not collect logs; call [ProcessHandle.Output] afterwards for stdout and stderr.
func (h *ProcessHandle) Wait(ctx context.Context, timeoutMs ...int) (*toolbox.ProcessResult, error) {
	request := h.service.toolboxClient.ProcessAPI.WaitForProcess(ctx, h.processID)
	if len(timeoutMs) > 0 {
		if timeoutMs[0] < 0 {
			return nil, sdkerrors.NewDaytonaError("timeoutMs must be non-negative", http.StatusBadRequest, nil)
		}
		request = request.TimeoutMs(int32(timeoutMs[0]))
	}
	result, httpResp, err := request.Execute()
	if err != nil {
		return nil, sdkerrors.ConvertToolboxError(err, httpResp)
	}
	return result, nil
}

// Output replays retained logs and returns collected stdout, stderr, and terminal
// metadata. Pair it with [ProcessHandle.Wait]; use [ProcessService.Run] when a
// single call should wait and collect output automatically.
func (h *ProcessHandle) Output(ctx context.Context) (*ProcessOutput, error) {
	record, err := h.Get(ctx)
	if err != nil {
		return nil, err
	}
	stdout, stderr, err := h.collectOutput(ctx)
	if err != nil {
		return nil, err
	}
	return &ProcessOutput{Stdout: stdout, Stderr: stderr, ExitCode: record.ExitCode, Signal: record.Signal, Reason: record.Reason}, nil
}

// Cleanup removes retained process metadata and logs. Processes started with
// [ProcessService.Start] retain logs until Cleanup; do not use the handle afterwards.
func (h *ProcessHandle) Cleanup(ctx context.Context) error {
	httpResp, err := h.service.toolboxClient.ProcessAPI.CleanupProcess(ctx, h.processID).Execute()
	return sdkerrors.ConvertToolboxError(err, httpResp)
}

// AttachTerminal opens an interactive WebSocket for a PTY process. It is PTY-only;
// use [ProcessHandle.StreamLogs] to observe output from non-interactive processes.
func (h *ProcessHandle) AttachTerminal(ctx context.Context) (*websocket.Conn, error) {
	record, err := h.Get(ctx)
	if err != nil {
		return nil, err
	}
	if record.Kind != toolbox.PROCESSKIND_KindPty {
		return nil, sdkerrors.NewDaytonaError("attach is only supported for kind=pty processes", http.StatusBadRequest, nil)
	}
	baseURL := h.service.toolboxClient.GetConfig().Servers[0].URL
	endpoint := fmt.Sprintf("%s/processes/%s/attach", common.ConvertToWebSocketURL(strings.TrimRight(baseURL, "/")), url.PathEscape(h.processID))
	headers := http.Header{}
	for key, value := range h.service.toolboxClient.GetConfig().DefaultHeader {
		headers.Set(key, value)
	}
	conn, response, err := websocket.DefaultDialer.DialContext(ctx, endpoint, headers)
	if err == nil {
		return conn, nil
	}
	if response == nil {
		return nil, sdkerrors.NewDaytonaConnectionError(fmt.Sprintf("attach process terminal: %v", err))
	}
	defer response.Body.Close()
	body, readErr := io.ReadAll(response.Body)
	if readErr != nil {
		return nil, sdkerrors.NewDaytonaError(fmt.Sprintf("attach process terminal: %v", err), response.StatusCode, response.Header)
	}
	return nil, sdkerrors.NewDaytonaErrorFromBody(body, response.StatusCode, response.Header)
}
