// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"time"
)

// SessionStartOptions configures one queued command, not the persistent shell.
type SessionStartOptions struct {
	// Name is an optional command alias.
	Name string
	// Stdin defaults to closed; open permits writing through the returned handle.
	Stdin ProcessStdinMode
	// ExecutionTimeout starts when the command starts, after earlier queued commands.
	// Zero or NoTimeout disables it. The shell normally survives a command timeout.
	ExecutionTimeout time.Duration
	// KeepLogs defaults to on_exit_ttl.
	KeepLogs ProcessKeepLogs
}

// SessionRunOptions configures a blocking session command.
type SessionRunOptions struct {
	// Name is an optional command alias.
	Name string
	// ExecutionTimeout stops the command on expiry, normally preserving the shell.
	ExecutionTimeout time.Duration
	// KeepLogs defaults to on_exit_ttl; none is refused.
	KeepLogs ProcessKeepLogs
	// Timeout bounds the caller's wait, without killing. See ExecOptions.Timeout
	// for defaults and streaming creation semantics.
	Timeout time.Duration
	// Stdout receives live output; writes are serialized with Stderr.
	// Failures preserve the partial result as described on ExecOptions.Stdout.
	Stdout io.Writer
	// Stderr receives live error output.
	Stderr io.Writer
}

// Start queues a command in this shell and returns its handle immediately.
// ErrSessionNotFound and ErrSessionEnded surface unchanged; no shell is recreated.
// A malformed create response with a valid ID returns a reconnectable handle alongside the error.
func (s *Session) Start(ctx context.Context, command string, opts *SessionStartOptions) (*ProcessHandle, error) {
	options := SessionStartOptions{}
	if opts != nil {
		options = *opts
	}
	body, err := processStartBody(&command, nil, &ProcessStartOptions{
		Name: options.Name, Stdin: options.Stdin,
		ExecutionTimeout: options.ExecutionTimeout, KeepLogs: options.KeepLogs,
	})
	if err != nil {
		return nil, err
	}
	body.SessionID = s.ID()
	return s.handle.sandbox.Process().start(ctx, body, 0)
}

// Run executes in the persistent shell and collects output. Non-zero ExitCode
// is not an error. Shell configuration belongs on CreateSession, not this call.
// Any failure after creation preserves ProcessID and collected output with unknown Reason.
func (s *Session) Run(ctx context.Context, command string, opts *SessionRunOptions) (*ProcessResult, error) {
	options := ExecOptions{}
	if opts != nil {
		options.Name, options.ExecutionTimeout, options.KeepLogs = opts.Name, opts.ExecutionTimeout, opts.KeepLogs
		options.Timeout, options.Stdout, options.Stderr = opts.Timeout, opts.Stdout, opts.Stderr
	}
	body, err := processStartBody(&command, nil, options.startOptions())
	if err != nil {
		return nil, err
	}
	body.SessionID = s.ID()
	return s.handle.sandbox.Process().run(ctx, body, options)
}
