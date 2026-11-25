// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"maps"
	"time"
)

// RunCodeOptions configures code execution. Nil selects the shared Python kernel.
// A writer or callback error stops delivery and attempts to interrupt the cell
// with a detached five-second budget, as on Timeout. Run returns a partial result
// with Process.ProcessID and unknown Reason alongside an SDK error preserving the
// local cause through errors.Is. Cleanup is best-effort: its failure does not
// replace the local error, and a successful interrupt does not confirm cell exit.
// Writers and callbacks run synchronously and must not block indefinitely.
// Any other failure after process creation (a transport or stream error) returns
// the partial result with Process.ProcessID and unknown Reason alongside the
// error, including without writers or callbacks; a run the sandbox ended before
// the code reached a kernel returns ProcessReasonFailed with ErrKernelUnavailable
// (see Sandbox.RunCode). Other failures do not retry the stream or trigger
// additional interruption; Timeout retains its documented interrupt semantics.
// Reconnect that process to wait, resume its logs, or interrupt it explicitly.
type RunCodeOptions struct {
	// Language selects the kernel language; empty defaults to python or Context's language.
	Language string
	// Context selects an existing context. It cannot be combined with ContextID or Isolated.
	Context *InterpreterContext
	// ContextID selects an existing context by ID and requires Language.
	ContextID string
	// Isolated creates a throwaway context and deletes it best-effort after execution.
	Isolated bool
	// Cwd overrides the execution's working directory.
	Cwd string
	// Env sets environment variables for implicit or isolated runs. Nil omits env;
	// an empty non-nil map sends {}. An explicit context takes its environment
	// at creation and rejects even an empty Env map.
	Env map[string]string
	// Timeout bounds queueing, setup and execution; expiry interrupts the kernel.
	// Zero waits indefinitely while keeping the client's setup request timeout.
	// WaitForever, NoTimeout and durations above the timer maximum remove both bounds.
	Timeout time.Duration
	// Stdout receives stdout frames as they arrive, while still retaining Logs.
	Stdout io.Writer
	// Stderr receives stderr frames as they arrive, while still retaining Logs.
	Stderr io.Writer
	// OnOutput receives rich outputs serially; an error ends delivery.
	OnOutput func(InterpreterOutput) error
	// OnError receives kernel exceptions serially; an error ends delivery.
	OnError func(InterpreterError) error
	// OnLog receives every frame serially, including setup and unknown channels.
	// Failures use the partial-result and cleanup semantics described on RunCodeOptions.
	OnLog func(LogFrame) error
}

// RunCode executes code through Interpreter.Run. Context-free runs share state
// per language. Explicit context loss returns ErrSessionNotFound without retry.
//
// The first run of a language starts its kernel and installs it when the image
// does not ship it: Python installs ipykernel from PyPI (pypi.org,
// files.pythonhosted.org) unless the image's Python has it and its venv module;
// TypeScript and JavaScript download Deno from dl.deno.land unless deno is on
// the image's PATH.
// That run takes a few seconds longer, and a network policy blocking those hosts
// prevents the install: allow them or use an image that ships the kernel. When
// the sandbox ends the run before the code reaches a kernel (most often a kernel
// that could not be installed or started), RunCode returns ErrKernelUnavailable
// (source SDK) carrying the sandbox's setup diagnostic, alongside the partial
// result with ProcessReasonFailed. The sandbox reports no error code for such a
// run: a context already gone when the run is submitted returns
// ErrSessionNotFound, but one deleted while the run waits behind another run in
// it ends the run this way and returns ErrKernelUnavailable. A kernel lost
// mid-run returns its partial output with ProcessReasonLost and a nil error; the
// next run starts a new kernel.
func (s *Sandbox) RunCode(ctx context.Context, code string, opts *RunCodeOptions) (*RunCodeResult, error) {
	return s.Interpreter().Run(ctx, code, opts)
}

// Run executes code and returns stdout, stderr, rich outputs and kernel errors.
// Timeout includes time queued behind another run in the same context. An
// expired queued run returns ProcessReasonTimedOut with an empty ProcessID.
// Callbacks must not synchronously run more code in the same context. A run the
// sandbox ended before the code reached a kernel returns ErrKernelUnavailable
// with the partial result, as described on Sandbox.RunCode.
func (s *Interpreter) Run(ctx context.Context, code string, opts *RunCodeOptions) (*RunCodeResult, error) {
	options := RunCodeOptions{}
	if opts != nil {
		options = *opts
		options.Env = maps.Clone(opts.Env)
	}
	selection, err := s.selectContext(options)
	if err != nil {
		return nil, err
	}
	timeout, err := processTimeout("Timeout", options.Timeout)
	if err != nil {
		return nil, err
	}
	if err = s.sandbox.client.ensureOpen(); err != nil {
		return nil, err
	}
	budget := runCodeBudget{requestTimeout: options.Timeout}
	if timeout > 0 {
		budget.deadline = s.now().Add(timeout)
	}
	if options.Isolated {
		return s.runIsolated(ctx, code, runCodeExecution{options: options, selection: selection, budget: budget})
	}
	return s.runQueued(ctx, code, runCodeExecution{options: options, selection: selection, budget: budget})
}

type interpreterSelection struct {
	id       string
	language string
}

type runCodeBudget struct {
	requestTimeout time.Duration
	deadline       time.Time
}

func (b runCodeBudget) remaining(now time.Time) time.Duration {
	if b.deadline.IsZero() {
		return 0
	}
	return b.deadline.Sub(now)
}

func (b runCodeBudget) setupTimeout(now time.Time) time.Duration {
	if !b.deadline.IsZero() {
		return max(time.Nanosecond, b.remaining(now))
	}
	if b.requestTimeout != 0 {
		return NoTimeout
	}
	return 0
}

type runCodeExecution struct {
	options   RunCodeOptions
	selection interpreterSelection
	budget    runCodeBudget
}

func (s *Interpreter) selectContext(opts RunCodeOptions) (interpreterSelection, error) {
	if opts.Context != nil && opts.ContextID != "" {
		return interpreterSelection{}, badRequest("Context and ContextID cannot both be supplied")
	}
	explicit := opts.Context != nil || opts.ContextID != ""
	if opts.Isolated && explicit {
		return interpreterSelection{}, badRequest("Isolated cannot target an explicit interpreter context")
	}
	if explicit && opts.Env != nil {
		return interpreterSelection{}, badRequest("Env is only supported when creating a context or running implicitly")
	}
	if opts.Context != nil {
		return s.selectBoundContext(opts.Context, opts.Language)
	}
	if opts.ContextID != "" {
		bound, err := s.ConnectContext(opts.ContextID, opts.Language)
		if err != nil {
			return interpreterSelection{}, err
		}
		return interpreterSelection{id: bound.id, language: bound.language}, nil
	}
	language := opts.Language
	if language == "" {
		language = "python"
	}
	return interpreterSelection{language: language}, nil
}

func (s *Interpreter) selectBoundContext(
	bound *InterpreterContext, language string,
) (interpreterSelection, error) {
	if bound.service == nil {
		return interpreterSelection{}, badRequest("Context must be obtained from Interpreter")
	}
	rebound, err := s.ConnectContextJSON(bound.Identity())
	if err != nil {
		return interpreterSelection{}, err
	}
	if language != "" && language != rebound.language {
		return interpreterSelection{}, badRequest("Language does not match the context's language")
	}
	return interpreterSelection{id: rebound.id, language: rebound.language}, nil
}
