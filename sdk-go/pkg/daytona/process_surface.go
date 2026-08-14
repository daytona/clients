// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"strings"

	sdkerrors "github.com/daytona/clients/sdk-go/pkg/errors"
	"github.com/daytona/clients/sdk-go/pkg/options"
	"github.com/daytona/clients/sdk-go/pkg/types"
	toolbox "github.com/daytona/clients/toolbox-api-client-go"
)

// ProcessRunResult is the completed result of [ProcessService.Run]. Unlike a
// background [ProcessHandle], it already includes collected output and status.
type ProcessRunResult struct {
	ID       string                        // Process ID.
	Handle   *ProcessHandle                // Handle for final inspection before short-TTL logs expire.
	Stdout   string                        // Collected standard output.
	Stderr   string                        // Collected standard error.
	ExitCode *int32                        // Exit code, when the process exited normally.
	Signal   *string                       // Signal that terminated the process, when applicable.
	Reason   toolbox.ProcessTerminalReason // Terminal reason reported by the daemon.
	TimedOut bool                          // Whether execution reached its wait timeout.
}

// Exec is the short-name equivalent of [ProcessService.ExecuteCommand]. Use it
// for the legacy synchronous shell-command API; use Run for the unified process API.
func (p *ProcessService) Exec(ctx context.Context, command string, opts ...func(*options.ExecuteCommand)) (*types.ExecuteResponse, error) {
	return p.ExecuteCommand(ctx, command, opts...)
}

// Start launches a background process and returns immediately with a handle. Use
// the handle to stream logs, write stdin, kill, Wait then Output, and Cleanup;
// reconnect later with [ProcessService.Connect] and the handle's ID. Logs remain
// retained until Cleanup. Use Run for one-shot execution.
func (p *ProcessService) Start(ctx context.Context, opts ...func(*options.ProcessStart)) (*ProcessHandle, error) {
	return withInstrumentation(ctx, p.otel, "Process", "Start", func(ctx context.Context) (*ProcessHandle, error) {
		return p.start(ctx, options.Apply(opts...))
	})
}

// Run launches a one-shot process, waits for completion, and returns collected
// output. Its logs default to the short on_exit_ttl policy; use Start when the
// caller must supervise, reconnect, or explicitly clean up retained logs.
func (p *ProcessService) Run(ctx context.Context, opts ...func(*options.ProcessRun)) (*ProcessRunResult, error) {
	return withInstrumentation(ctx, p.otel, "Process", "Run", func(ctx context.Context) (*ProcessRunResult, error) {
		runOpts := options.Apply(opts...)
		if runOpts.WaitTimeoutMs != nil && *runOpts.WaitTimeoutMs < 0 {
			return nil, sdkerrors.NewDaytonaError("waitTimeoutMs must be non-negative", http.StatusBadRequest, nil)
		}
		if runOpts.KeepLogs == nil {
			keepLogs := string(toolbox.PROCESSKEEPLOGS_KeepLogsOnExitTTL)
			runOpts.KeepLogs = &keepLogs
		}
		handle, err := p.start(ctx, runOpts)
		if err != nil {
			return nil, err
		}

		var stdout, stderr string
		var result *toolbox.ProcessResult
		streamTimedOut := false
		if runOpts.OnStdout != nil || runOpts.OnStderr != nil {
			stdout, stderr, streamTimedOut, err = collectProcessStream(ctx, handle, runOpts)
			if err != nil {
				return nil, err
			}
			waitTimeout := []int(nil)
			if streamTimedOut {
				waitTimeout = []int{1}
			}
			result, err = handle.Wait(ctx, waitTimeout...)
		} else {
			waitTimeout := []int(nil)
			if runOpts.WaitTimeoutMs != nil {
				waitTimeout = []int{*runOpts.WaitTimeoutMs}
			}
			result, err = handle.Wait(ctx, waitTimeout...)
			if err == nil {
				stdout, stderr, err = handle.collectOutput(ctx)
			}
		}
		if err != nil {
			return nil, err
		}
		return &ProcessRunResult{
			ID: handle.ID(), Handle: handle, Stdout: stdout, Stderr: stderr,
			ExitCode: result.ExitCode, Signal: result.Signal, Reason: result.Reason,
			TimedOut: streamTimedOut || result.Reason == toolbox.PROCESSTERMINALREASON_ReasonTimedOut,
		}, nil
	})
}

// Get validates that a process exists and returns a handle for its ID. Use it to
// reconnect to a process instead of serializing handles; Connect is its parity alias.
func (p *ProcessService) Get(ctx context.Context, id string) (*ProcessHandle, error) {
	id = strings.TrimSpace(id)
	if id == "" {
		return nil, sdkerrors.NewDaytonaError("processId must not be blank", http.StatusBadRequest, nil)
	}
	_, httpResp, err := p.toolboxClient.ProcessAPI.GetProcess(ctx, id).Execute()
	if err != nil {
		return nil, sdkerrors.ConvertToolboxError(err, httpResp)
	}
	return newProcessHandle(id, p), nil
}

// Connect is an alias of Get. Use it with a process ID from Start when
// reconnecting from another client instance.
func (p *ProcessService) Connect(ctx context.Context, id string) (*ProcessHandle, error) {
	return p.Get(ctx, id)
}

// List returns supervised processes matching optional filters. Use Get or Connect
// when the process ID is already known.
func (p *ProcessService) List(ctx context.Context, opts ...func(*options.ProcessList)) ([]toolbox.Process, error) {
	listOpts := options.Apply(opts...)
	request := p.toolboxClient.ProcessAPI.ListProcesses(ctx)
	if listOpts.State != nil {
		request = request.State(*listOpts.State)
	}
	if listOpts.Kind != nil {
		request = request.Kind(*listOpts.Kind)
	}
	if listOpts.Name != nil {
		request = request.Name(*listOpts.Name)
	}
	if listOpts.SessionID != nil {
		request = request.SessionId(*listOpts.SessionID)
	}
	processes, httpResp, err := request.Execute()
	if err != nil {
		return nil, sdkerrors.ConvertToolboxError(err, httpResp)
	}
	return processes, nil
}

func (p *ProcessService) start(ctx context.Context, startOpts *options.ProcessStart) (*ProcessHandle, error) {
	request, err := buildCreateProcessRequest(startOpts)
	if err != nil {
		return nil, err
	}
	process, httpResp, err := p.toolboxClient.ProcessAPI.CreateProcess(ctx).Request(*request).Execute()
	if err != nil {
		return nil, sdkerrors.ConvertToolboxError(err, httpResp)
	}
	return newProcessHandle(process.Id, p), nil
}

func buildCreateProcessRequest(opts *options.ProcessStart) (*toolbox.CreateProcessRequest, error) {
	hasArgv := len(opts.Argv) > 0
	hasCommand := opts.ShellCommand != nil && strings.TrimSpace(*opts.ShellCommand) != ""
	isPTY := opts.Kind != nil && *opts.Kind == "pty"
	if hasArgv == hasCommand && (!isPTY || hasArgv || hasCommand) {
		return nil, sdkerrors.NewDaytonaError("provide exactly one of argv or shellCommand", http.StatusBadRequest, nil)
	}
	if opts.TimeoutMs != nil && *opts.TimeoutMs < 0 {
		return nil, sdkerrors.NewDaytonaError("timeoutMs must be non-negative", http.StatusBadRequest, nil)
	}
	if opts.Name != nil && strings.TrimSpace(*opts.Name) == "" {
		return nil, sdkerrors.NewDaytonaError("name must not be blank", http.StatusBadRequest, nil)
	}
	if opts.Terminal != nil && (opts.Terminal.GetCols() <= 0 || opts.Terminal.GetRows() <= 0) {
		return nil, sdkerrors.NewDaytonaError("terminal dimensions must be positive", http.StatusBadRequest, nil)
	}
	request := toolbox.NewCreateProcessRequest()
	if hasArgv {
		request.SetArgv(opts.Argv)
	}
	if hasCommand {
		request.SetShellCommand(strings.TrimSpace(*opts.ShellCommand))
	}
	if opts.Cwd != nil {
		request.SetCwd(*opts.Cwd)
	}
	if opts.Env != nil {
		request.SetEnv(opts.Env)
	}
	if opts.KeepLogs != nil {
		request.SetKeepLogs(toolbox.ProcessKeepLogs(*opts.KeepLogs))
	}
	if opts.Kind != nil {
		request.SetKind(toolbox.ProcessKind(*opts.Kind))
	}
	if opts.Login != nil {
		request.SetLogin(*opts.Login)
	}
	if opts.Name != nil {
		request.SetName(strings.TrimSpace(*opts.Name))
	}
	if opts.SessionID != nil {
		request.SetSessionId(*opts.SessionID)
	}
	if opts.Shell != nil {
		request.SetShell(toolbox.ProcessShellSelector(*opts.Shell))
	}
	if opts.Stdin != nil {
		request.SetStdin(toolbox.ProcessStdinMode(*opts.Stdin))
	}
	if opts.Terminal != nil {
		request.SetTerminal(*opts.Terminal)
	}
	if opts.TimeoutMs != nil {
		request.SetTimeoutMs(int32(*opts.TimeoutMs))
	}
	if opts.User != nil {
		request.SetUser(*opts.User)
	}
	return request, nil
}
