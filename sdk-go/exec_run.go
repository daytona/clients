// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

func (s *Processes) run(
	ctx context.Context, body processCreateRequest, options ExecOptions,
) (*ProcessResult, error) {
	timeout, err := processTimeout("Timeout", options.Timeout)
	if err != nil {
		return nil, err
	}
	if body.KeepLogs == ProcessKeepLogsNone {
		return nil, badRequest("KeepLogs must retain logs for blocking runs")
	}
	if options.Stdout != nil || options.Stderr != nil {
		return s.runStreaming(ctx, body, options)
	}
	// The create request holds one wait round at most (the budget when it is
	// shorter); the rest of the wait goes on in rounds. It is never retried:
	// a failed create may still have started the process.
	hold := processWaitRound
	if timeout > 0 {
		hold = min(hold, timeout)
	}
	milliseconds := wireMilliseconds(hold)
	requestTimeout := time.Duration(milliseconds)*time.Millisecond + waitReadSlack
	started := time.Now()
	var raw json.RawMessage
	if err = s.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/processes",
		Query: url.Values{"waitMs": {strconv.FormatInt(milliseconds, 10)}, "logs": {"text"}},
		Body:  body, Result: &raw, Timeout: requestTimeout,
	}); err != nil {
		return nil, err
	}
	info, err := parseCreatedProcess(raw)
	handle := s.startedHandle(info, body)
	if err != nil {
		return handle.creationFailureResult(), err
	}
	page, err := execInlineLogs(raw)
	if err != nil {
		return processResult(handle.id, WaitResult{Reason: ProcessReasonUnknown}, nil), err
	}
	wait := &info.WaitResult
	if info.State != ProcessStateTerminal {
		wait, err = handle.waitRemaining(ctx, started, timeout)
		if err != nil {
			var logs []LogFrame
			if page != nil {
				logs = page.Items
			}
			return processResult(handle.id, WaitResult{Reason: ProcessReasonUnknown}, logs), err
		}
	}
	info.WaitResult = *wait
	limit := 0
	if info.Reason == ProcessReasonTimedOut {
		limit = 8
	}
	logs, err := handle.collectLogs(ctx, limit, page)
	if err != nil {
		return processResult(handle.id, WaitResult{Reason: ProcessReasonUnknown}, logs), err
	}
	return processResult(handle.id, *wait, logs), nil
}

func (h *ProcessHandle) waitRemaining(
	ctx context.Context, started time.Time, budget time.Duration,
) (*WaitResult, error) {
	remaining := budget
	if budget > 0 {
		remaining -= time.Since(started)
		if remaining <= 0 {
			return &WaitResult{Reason: ProcessReasonTimedOut}, nil
		}
	}
	return h.Wait(ctx, &WaitProcessOptions{Timeout: remaining})
}

func execInlineLogs(raw json.RawMessage) (*ProcessLogPage, error) {
	object, err := parseObject(raw, "process")
	if err != nil {
		return nil, err
	}
	if present(object["logs"]) {
		return parseProcessLogPage(object["logs"], LogEncodingText)
	}
	return nil, nil //nolint:nilnil // An absent optional inline page is valid; replay starts at the first page.
}

func processResult(id string, wait WaitResult, logs []LogFrame) *ProcessResult {
	var stdout, stderr, output strings.Builder
	for _, frame := range logs {
		switch frame.Channel {
		case LogChannelStdout, LogChannelPTY:
			stdout.WriteString(frame.Data)
			output.WriteString(frame.Data)
		case LogChannelStderr:
			stderr.WriteString(frame.Data)
			output.WriteString(frame.Data)
		case LogChannelSetup, LogChannelDisplay, LogChannelExecuteResult, LogChannelError, LogChannelUnknown:
			// Provisioning and rich results are not command output.
		}
	}
	return &ProcessResult{
		WaitResult: wait, ProcessID: id, Stdout: stdout.String(), Stderr: stderr.String(),
		Output: output.String(), Logs: logs,
	}
}
