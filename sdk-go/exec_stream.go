// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"io"
	"time"
)

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
		return nil, err
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
			return nil, waitErr
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
	snapshot, readErr := processCollectLogs(ctx, handle, 8)
	if readErr != nil {
		return nil, readErr
	}
	seen := make(map[string]bool, len(logs))
	for _, frame := range logs {
		seen[frame.Cursor] = true
	}
	// Preserve replay semantics; exclude only overlap with the live stream.
	for _, frame := range snapshot {
		if !seen[frame.Cursor] {
			logs = append(logs, frame)
		}
	}
	return processResult(handle.id, WaitResult{Reason: ProcessReasonTimedOut}, logs), nil
}

func (o ExecOptions) consume(ctx context.Context, handle *ProcessHandle) ([]LogFrame, error) {
	logs := make([]LogFrame, 0)
	for frame, err := range handle.Logs(ctx, nil) {
		if err != nil {
			return logs, err
		}
		if ctx.Err() != nil {
			return logs, transportError(ctx, ctx.Err())
		}
		var writer io.Writer
		switch frame.Channel {
		case LogChannelStdout, LogChannelPTY:
			writer = o.Stdout
		case LogChannelStderr:
			writer = o.Stderr
		case LogChannelSetup, LogChannelDisplay, LogChannelExecuteResult, LogChannelError, LogChannelUnknown:
			// Other frames remain available through the result.
		}
		logs = append(logs, frame)
		if writer != nil {
			var written int
			written, err = io.WriteString(writer, frame.Data)
			if err == nil && written != len(frame.Data) {
				err = io.ErrShortWrite
			}
			if err != nil {
				return logs, &outputDeliveryError{wrapError(err, "write process output")}
			}
		}
	}
	return logs, nil
}
