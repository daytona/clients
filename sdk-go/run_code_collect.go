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
	output := newRunCodeOutput(options)
	wait, err := s.waitCode(ctx, handle, execution.budget)
	if err != nil {
		return output.finish(InterpreterProcess{
			ProcessID: handle.ID(), WaitResult: WaitResult{Reason: ProcessReasonUnknown},
		}), err
	}
	if err = output.collect(ctx, handle, wait); err != nil {
		return output.finish(InterpreterProcess{
			ProcessID: handle.ID(), WaitResult: WaitResult{Reason: ProcessReasonUnknown},
		}), err
	}
	return output.complete(handle.ID(), *wait)
}

func (o *runCodeOutput) collect(ctx context.Context, handle *ProcessHandle, wait *WaitResult) error {
	maxPages := 0
	if wait.Reason == ProcessReasonTimedOut {
		maxPages = 8
	}
	frames, readErr := processCollectLogs(ctx, handle, maxPages)
	for _, frame := range frames {
		if err := o.append(frame); err != nil {
			return err
		}
	}
	return readErr
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
		handle.stopAfterOutputFailure(ctx, ProcessSignalInterrupt)
		return output.finish(InterpreterProcess{
			ProcessID: handle.ID(), WaitResult: WaitResult{Reason: ProcessReasonUnknown},
		}), streamErr
	}
	if streamErr != nil && !errors.Is(streamErr, context.Canceled) {
		return output.finish(InterpreterProcess{
			ProcessID: handle.ID(), WaitResult: WaitResult{Reason: ProcessReasonUnknown},
		}), streamErr
	}
	if wait.err != nil {
		return output.finish(InterpreterProcess{
			ProcessID: handle.ID(), WaitResult: WaitResult{Reason: ProcessReasonUnknown},
		}), wait.err
	}
	if streamErr != nil && wait.result.Reason != ProcessReasonTimedOut {
		return output.finish(InterpreterProcess{
			ProcessID: handle.ID(), WaitResult: WaitResult{Reason: ProcessReasonUnknown},
		}), streamErr
	}
	if wait.result.Reason == ProcessReasonTimedOut {
		// The kernel may still be running after interrupt grace. Read a bounded
		// snapshot and deduplicate frames already delivered through the stream.
		if err := output.collect(ctx, handle, wait.result); err != nil {
			var deliveryErr *outputDeliveryError
			if errors.As(err, &deliveryErr) {
				handle.stopAfterOutputFailure(ctx, ProcessSignalInterrupt)
			}
			return output.finish(InterpreterProcess{
				ProcessID: handle.ID(), WaitResult: WaitResult{Reason: ProcessReasonUnknown},
			}), err
		}
	}
	return output.complete(handle.ID(), *wait.result)
}

func (o *runCodeOutput) stream(ctx context.Context, handle *ProcessHandle) (bool, error) {
	for event, err := range handle.StreamLogs(ctx, nil) {
		if ctx.Err() != nil {
			return false, transportError(ctx, ctx.Err())
		}
		if err != nil {
			return false, err
		}
		switch value := event.(type) {
		case ProcessLogFrameEvent:
			if err = o.append(value.Frame); err != nil {
				var deliveryErr *outputDeliveryError
				return errors.As(err, &deliveryErr), err
			}
		case ProcessLogStateEvent, ProcessLogWarningEvent, ProcessLogEOFEvent:
			// Wait owns the outcome; only log frames contribute interpreter output.
		}
	}
	return false, nil
}
