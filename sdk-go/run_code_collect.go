// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
)

func (s *Interpreter) collectCode(
	ctx context.Context, handle *ProcessHandle, execution runCodeExecution,
) (*RunCodeResult, error) {
	options := execution.options
	if options.Stdout != nil || options.Stderr != nil ||
		options.OnOutput != nil || options.OnError != nil || options.OnLog != nil {
		return s.streamCode(ctx, handle, execution)
	}
	wait, err := s.waitCode(ctx, handle, execution.budget)
	if err != nil {
		return nil, err
	}
	output := newRunCodeOutput(options)
	if err = output.collect(ctx, handle, wait); err != nil {
		return nil, err
	}
	return output.finish(InterpreterProcess{ProcessID: handle.ID(), WaitResult: *wait}), nil
}

func (o *runCodeOutput) collect(ctx context.Context, handle *ProcessHandle, wait *WaitResult) error {
	maxPages := 0
	if wait.Reason == ProcessReasonTimedOut {
		maxPages = 8
	}
	frames, err := processCollectLogs(ctx, handle, maxPages)
	if err != nil {
		return err
	}
	for _, frame := range frames {
		if err = o.append(frame); err != nil {
			return err
		}
	}
	return nil
}

type runCodeWait struct {
	result *WaitResult
	err    error
}

func (s *Interpreter) streamCode(
	ctx context.Context, handle *ProcessHandle, execution runCodeExecution,
) (*RunCodeResult, error) {
	waitCtx, cancelWait := context.WithCancel(ctx)
	defer cancelWait()
	streamCtx, cancelStream := context.WithCancel(ctx)
	defer cancelStream()
	done := make(chan runCodeWait, 1)
	go func() {
		result, err := s.waitCode(waitCtx, handle, execution.budget)
		if err != nil || result.Reason == ProcessReasonTimedOut {
			cancelStream()
		}
		done <- runCodeWait{result: result, err: err}
	}()
	output := newRunCodeOutput(execution.options)
	deliveryFailed, streamErr := output.stream(streamCtx, handle)
	if streamErr != nil {
		cancelWait()
	}
	wait := <-done // Always join the waiter before returning or releasing the queue.
	if deliveryFailed {
		return nil, streamErr
	}
	if streamErr != nil && !errors.Is(streamErr, context.Canceled) {
		return nil, streamErr
	}
	if wait.err != nil {
		return nil, wait.err
	}
	if streamErr != nil && wait.result.Reason != ProcessReasonTimedOut {
		return nil, streamErr
	}
	if wait.result.Reason == ProcessReasonTimedOut {
		// The kernel may still be running after interrupt grace. Read a bounded
		// snapshot and deduplicate frames already delivered through the stream.
		if err := output.collect(ctx, handle, wait.result); err != nil {
			return nil, err
		}
	}
	return output.finish(InterpreterProcess{ProcessID: handle.ID(), WaitResult: *wait.result}), nil
}

func (o *runCodeOutput) stream(ctx context.Context, handle *ProcessHandle) (bool, error) {
	for event, err := range handle.StreamLogs(ctx, nil) {
		if err != nil {
			return false, err
		}
		switch value := event.(type) {
		case ProcessLogFrameEvent:
			if err = o.append(value.Frame); err != nil {
				return true, err
			}
		case ProcessLogStateEvent, ProcessLogWarningEvent, ProcessLogEOFEvent:
			// Wait owns the outcome; only log frames contribute interpreter output.
		}
	}
	return false, nil
}
