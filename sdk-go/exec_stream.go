// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"io"
	"time"
)

// execFollowIdle bounds how long a run's output stream may deliver nothing
// before it is treated as dropped and resumed from the last event's cursor.
// A healthy quiet stream costs one reconnect; a silently dropped one no
// longer holds the run until its timeout.
var execFollowIdle = 45 * time.Second

// execDeadlineReadBound bounds the process read after a run's timeout, like
// the read slack of a wait round, so a stalled read cannot stretch the run.
var execDeadlineReadBound = 5 * time.Second

func (s *Processes) runStreaming(
	ctx context.Context, body processCreateRequest, options ExecOptions,
) (*ProcessResult, error) {
	// run already validated the timeout before choosing the streaming path.
	budget := boundedTimeout(options.Timeout)
	requestTimeout := time.Duration(0)
	if options.Timeout != 0 && budget == 0 {
		requestTimeout = NoTimeout
	}
	handle, err := s.start(ctx, body, requestTimeout)
	if err != nil {
		return handle.creationFailureResult(), err
	}
	started := time.Now()
	streamCtx, cancel := context.WithCancel(ctx)
	if budget > 0 {
		cancel()
		streamCtx, cancel = context.WithTimeout(ctx, budget)
	}
	defer cancel()
	logs, err := options.consume(streamCtx, handle)
	if err == nil {
		wait, waitErr := handle.waitRemaining(ctx, started, budget)
		if waitErr != nil {
			return processResult(handle.id, WaitResult{Reason: ProcessReasonUnknown}, logs), waitErr
		}
		return processResult(handle.id, *wait, logs), nil
	}
	var deliveryErr *outputDeliveryError
	if errors.As(err, &deliveryErr) {
		handle.stopAfterOutputFailure(ctx, ProcessSignalTerminate)
		return processResult(handle.id, WaitResult{Reason: ProcessReasonUnknown}, logs), err
	}
	if ctx.Err() != nil {
		return processResult(handle.id, WaitResult{Reason: ProcessReasonUnknown}, logs), transportError(ctx, err)
	}
	if !errors.Is(streamCtx.Err(), context.DeadlineExceeded) || !errors.Is(err, context.DeadlineExceeded) {
		return processResult(handle.id, WaitResult{Reason: ProcessReasonUnknown}, logs), err
	}
	return handle.recoverExec(ctx, options, logs)
}

func (h *ProcessHandle) recoverExec(
	ctx context.Context, options ExecOptions, logs []LogFrame,
) (*ProcessResult, error) {
	cursor := ""
	if len(logs) > 0 {
		cursor = logs[len(logs)-1].Cursor
	}
	snapshot, readErr := h.collectLogPages(ctx, cursor, 8)
	seen := make(map[string]bool, len(logs))
	for _, frame := range logs {
		seen[frame.Cursor] = true
	}
	// Preserve replay semantics; exclude only overlap with the live stream.
	for _, frame := range snapshot {
		if !seen[frame.Cursor] {
			if ctx.Err() != nil {
				result := processResult(h.id, WaitResult{Reason: ProcessReasonUnknown}, logs)
				return result, transportError(ctx, ctx.Err())
			}
			logs = append(logs, frame)
			if err := options.deliver(frame); err != nil {
				h.stopAfterOutputFailure(ctx, ProcessSignalTerminate)
				return processResult(h.id, WaitResult{Reason: ProcessReasonUnknown}, logs), err
			}
		}
	}
	if ctx.Err() != nil {
		return processResult(h.id, WaitResult{Reason: ProcessReasonUnknown}, logs), transportError(ctx, ctx.Err())
	}
	if readErr != nil {
		return processResult(h.id, WaitResult{Reason: ProcessReasonUnknown}, logs), readErr
	}
	// A process that ended by the deadline reports how it ended, not timed_out.
	readCtx, cancel := context.WithTimeout(ctx, execDeadlineReadBound)
	defer cancel()
	if info, err := h.Get(readCtx); err == nil && info.State == ProcessStateTerminal {
		return processResult(h.id, info.WaitResult, logs), nil
	}
	return processResult(h.id, WaitResult{Reason: ProcessReasonTimedOut}, logs), nil
}

// consume follows the output until EOF, resuming from the last event's cursor
// whenever the stream goes idle for execFollowIdle.
func (o ExecOptions) consume(ctx context.Context, handle *ProcessHandle) ([]LogFrame, error) {
	logs := make([]LogFrame, 0)
	cursor := ""
	for {
		idle := false
		for event, err := range handle.streamLogs(ctx, &ProcessLogsOptions{Cursor: cursor}, execFollowIdle) {
			if errors.Is(err, errProcessLogIdle) {
				idle = true
				break
			}
			if err != nil {
				return logs, err
			}
			if ctx.Err() != nil {
				return logs, transportError(ctx, ctx.Err())
			}
			var frame *LogFrame
			switch value := event.(type) {
			case ProcessLogFrameEvent:
				cursor, frame = value.Cursor, &value.Frame
			case ProcessLogStateEvent:
				cursor = value.Cursor
			case ProcessLogWarningEvent:
				cursor = value.Cursor
			case ProcessLogEOFEvent:
				cursor = value.Cursor
			}
			if frame == nil {
				continue
			}
			logs = append(logs, *frame)
			if err = o.deliver(*frame); err != nil {
				return logs, err
			}
		}
		if !idle {
			return logs, nil
		}
	}
}

func (o ExecOptions) deliver(frame LogFrame) error {
	var writer io.Writer
	switch frame.Channel {
	case LogChannelStdout, LogChannelPTY:
		writer = o.Stdout
	case LogChannelStderr:
		writer = o.Stderr
	case LogChannelSetup, LogChannelDisplay, LogChannelExecuteResult, LogChannelError, LogChannelUnknown:
		// Other frames remain available through the result.
	}
	if writer == nil {
		return nil
	}
	written, err := io.WriteString(writer, frame.Data)
	if err == nil && written != len(frame.Data) {
		err = io.ErrShortWrite
	}
	if err != nil {
		return &outputDeliveryError{wrapError(err, "write process output")}
	}
	return nil
}
