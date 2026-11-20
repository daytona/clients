// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"time"
)

// ExecOptions configures a blocking command. Nil selects defaults.
type ExecOptions struct {
	// Cwd overrides the working directory.
	Cwd string
	// Env adds environment variables.
	Env map[string]string
	// User overrides the OS user.
	User string
	// Shell overrides automatic bash/sh selection for shell commands.
	Shell string
	// Login selects a login shell; nil leaves it to the sandbox.
	Login *bool
	// Name is an optional live process alias.
	Name string
	// Tag groups this process for List and KillAll.
	Tag string
	// Timeout bounds waiting, not execution. Zero, WaitForever or NoTimeout waits indefinitely.
	// Expiry returns reason timed_out; reconnect ProcessID to observe or stop the process.
	// With output writers, the wait budget starts after creation. The create request
	// uses the client timeout unless Timeout is explicitly unlimited.
	Timeout time.Duration
	// ExecutionTimeout is a server deadline that kills the process tree.
	// Zero or NoTimeout means no execution deadline.
	ExecutionTimeout time.Duration
	// KeepLogs defaults to on_exit_ttl. none is refused because output requires logs.
	KeepLogs ProcessKeepLogs
	// Stdout receives live stdout. Calls are synchronous; writers must not block indefinitely.
	Stdout io.Writer
	// Stderr receives live stderr, serialized with Stdout calls (the same writer is safe).
	Stderr io.Writer
}

// Exec runs a shell command and collects Stdout, Stderr, interleaved Output and Logs.
// A non-zero ExitCode is NOT an error: inspect res.ExitCode (nil means unreported).
// err means invalid input, cancellation, a transport/protocol failure or a server
// error; use errors.Is with ErrProcessNotFound, ErrSessionEnded, etc.
// Timeout limits observation without killing; ExecutionTimeout stops the tree.
// For untrusted arguments use ExecArgs; for persistent shell state use CreateSession.
func (s *Sandbox) Exec(ctx context.Context, command string, opts *ExecOptions) (*ProcessResult, error) {
	return s.Process().Run(ctx, command, opts)
}

// ExecArgs runs an argument vector without shell interpolation.
// argv must contain at least the executable. Results and timeout semantics match Exec.
func (s *Sandbox) ExecArgs(ctx context.Context, argv []string, opts *ExecOptions) (*ProcessResult, error) {
	return s.Process().RunArgs(ctx, argv, opts)
}

// Run is the process namespace form of Sandbox.Exec.
func (s *Processes) Run(ctx context.Context, command string, opts *ExecOptions) (*ProcessResult, error) {
	options := execOptions(opts)
	body, err := processStartBody(&command, nil, options.startOptions())
	if err != nil {
		return nil, err
	}
	return s.run(ctx, body, options)
}

// RunArgs is the process namespace form of Sandbox.ExecArgs.
func (s *Processes) RunArgs(ctx context.Context, argv []string, opts *ExecOptions) (*ProcessResult, error) {
	if len(argv) == 0 {
		return nil, badRequest("argv must not be empty")
	}
	options := execOptions(opts)
	body, err := processStartBody(nil, argv, options.startOptions())
	if err != nil {
		return nil, err
	}
	return s.run(ctx, body, options)
}

func execOptions(opts *ExecOptions) ExecOptions {
	if opts == nil {
		return ExecOptions{}
	}
	return *opts
}

func (o ExecOptions) startOptions() *ProcessStartOptions {
	return &ProcessStartOptions{
		Cwd: o.Cwd, Env: o.Env, User: o.User, Shell: o.Shell, Login: o.Login,
		Name: o.Name, Tag: o.Tag, ExecutionTimeout: o.ExecutionTimeout, KeepLogs: o.KeepLogs,
	}
}
