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
	budget, err := processTimeout(options.Timeout)
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
	requestTimeout := time.Duration(0)
	if options.Timeout != 0 && budget == 0 {
		requestTimeout = NoTimeout
	}
	handle, err := s.start(streamCtx, body, requestTimeout)
	if err != nil {
		return nil, err
	}
	logs, err := options.consume(streamCtx, handle)
	if err != nil {
		if ctx.Err() != nil {
			return nil, transportError(ctx, err)
		}
		if !errors.Is(streamCtx.Err(), context.DeadlineExceeded) {
			return nil, err
		}
		snapshot, readErr := processCollectLogs(ctx, handle, 8)
		if readErr != nil {
			return nil, readErr
		}
		return processResult(handle.id, WaitResult{Reason: ProcessReasonTimedOut}, snapshot), nil
	}
	wait, err := handle.waitRemaining(ctx, started, budget)
	if err != nil {
		return nil, err
	}
	return processResult(handle.id, *wait, logs), nil
}

func (o ExecOptions) consume(ctx context.Context, handle *ProcessHandle) ([]LogFrame, error) {
	logs := make([]LogFrame, 0)
	for frame, err := range handle.Logs(ctx, nil) {
		if err != nil {
			return nil, err
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
		if writer != nil {
			if _, err = io.WriteString(writer, frame.Data); err != nil {
				return nil, wrapError(err, "write process output")
			}
		}
		logs = append(logs, frame)
	}
	return logs, nil
}
