// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"crypto/rand"
	"errors"
	"time"
)

func (s *InterpreterService) runQueued(
	ctx context.Context, code string, execution runCodeExecution,
) (*RunCodeResult, error) {
	queueID := execution.selection.id
	if queueID == "" {
		queueID = "code-" + execution.selection.language
	}
	queueCtx, cancel := context.WithCancel(ctx)
	if !execution.budget.deadline.IsZero() {
		cancel()
		queueCtx, cancel = context.WithTimeout(ctx, execution.budget.remaining(s.now()))
	}
	defer cancel()
	release, err := s.acquire(queueCtx, queueID)
	if err != nil {
		if ctx.Err() == nil && errors.Is(err, context.DeadlineExceeded) {
			return timedOutRunCodeResult(), nil
		}
		return nil, err
	}
	defer release()
	if ctx.Err() != nil {
		return nil, transportError(ctx, ctx.Err())
	}
	if !execution.budget.deadline.IsZero() && execution.budget.remaining(s.now()) <= 0 {
		return timedOutRunCodeResult(), nil
	}
	handle, err := s.sandbox.Process().start(ctx, processCreateRequest{
		Code: &code, Kind: ProcessKindCode, Language: execution.selection.language,
		SessionID: execution.selection.id, Cwd: execution.options.Cwd,
		Env: execution.options.Env, KeepLogs: ProcessKeepLogsOnExitTTL,
	}, execution.budget.setupTimeout(s.now()))
	if err != nil {
		return nil, err
	}
	return s.collectCode(ctx, handle, execution)
}

func (s *InterpreterService) runIsolated(
	ctx context.Context, code string, execution runCodeExecution,
) (*RunCodeResult, error) {
	cleanupID := "isolated-" + execution.selection.language + "-" + rand.Text()
	defer func() {
		// Cleanup must still be attempted when the caller cancels. The detached
		// context is bounded, and the transport also observes Client.Close.
		cleanupCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 10*time.Second)
		defer cancel()
		if err := s.DeleteContext(cleanupCtx, cleanupID); err != nil {
			// Throwaway cleanup is best-effort and must not mask the execution's
			// result, including setup errors where the context may not exist.
			return
		}
	}()
	bound, err := s.createContext(ctx, interpreterContextRequest{
		ID: cleanupID, Kernel: execution.selection.language, Cwd: execution.options.Cwd, Env: execution.options.Env,
	}, execution.budget.setupTimeout(s.now()))
	if err != nil {
		return nil, err
	}
	cleanupID = bound.id
	if !execution.budget.deadline.IsZero() && execution.budget.remaining(s.now()) <= 0 {
		return nil, newSDKError("REQUEST_TIMEOUT", 408, "interpreter run timed out creating its isolated context")
	}
	isolated := execution
	isolated.selection.id = bound.id
	isolated.options.Env = nil
	return s.runQueued(ctx, code, isolated)
}

func timedOutRunCodeResult() *RunCodeResult {
	return &RunCodeResult{
		Process: InterpreterProcess{WaitResult: WaitResult{Reason: ProcessReasonTimedOut}},
		Results: []InterpreterOutput{},
	}
}

func (s *InterpreterService) waitCode(
	ctx context.Context, handle *ProcessHandle, budget runCodeBudget,
) (*WaitResult, error) {
	result := &WaitResult{Reason: ProcessReasonTimedOut}
	remaining := budget.remaining(s.now())
	if budget.deadline.IsZero() || remaining > 0 {
		waited, err := handle.Wait(ctx, &WaitProcessOptions{Timeout: remaining})
		if err != nil {
			return nil, err
		}
		result = waited
	}
	if result.Reason != ProcessReasonTimedOut || result.ExitCode != nil {
		return result, nil
	}
	deadline := s.now().Add(10 * time.Second)
	for s.now().Before(deadline) {
		err := handle.Kill(ctx, &KillProcessOptions{Signal: ProcessSignalInterrupt})
		switch {
		case err == nil, errors.Is(err, ErrProcessTerminal):
			return handle.Wait(ctx, &WaitProcessOptions{Timeout: 10 * time.Second})
		case errors.Is(err, ErrUnsupportedOperation):
			waited, waitErr := handle.Wait(ctx, &WaitProcessOptions{Timeout: 250 * time.Millisecond})
			if waitErr != nil {
				return nil, waitErr
			}
			if waited.Reason != ProcessReasonTimedOut || waited.ExitCode != nil {
				return waited, nil
			}
		default:
			return nil, err
		}
	}
	return result, nil
}
