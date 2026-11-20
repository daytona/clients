// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"time"
)

// PTY manages interactive terminals in one sandbox.
// Obtain it from Sandbox.PTY.
type PTY struct{ sandbox *Sandbox }

// PTY returns the sandbox's interactive terminal namespace.
func (s *Sandbox) PTY() *PTY { return &PTY{sandbox: s} }

// Start starts a detached terminal. An empty command selects the sandbox's
// default shell. Defaults are 80 columns, 24 rows, TERM=xterm-256color, open
// stdin and logs retained until cleanup. Invalid options return ErrBadRequest.
func (s *PTY) Start(ctx context.Context, command string, opts *PTYOptions) (*ProcessHandle, error) {
	options := PTYOptions{}
	if opts != nil {
		options = *opts
	}
	start := ProcessStartOptions{
		Cwd: options.Cwd, Env: options.Env, Name: options.Name, Tag: options.Tag,
		User: options.User, Shell: options.Shell, Login: options.Login,
		Stdin: options.Stdin, ExecutionTimeout: options.ExecutionTimeout, KeepLogs: options.KeepLogs,
	}
	if start.Stdin == "" {
		start.Stdin = ProcessStdinOpen
	}
	if start.KeepLogs == "" {
		start.KeepLogs = ProcessKeepLogsUntilCleanup
	}
	var shellCommand *string
	if command != "" {
		shellCommand = &command
	}
	body, err := processStartBody(shellCommand, nil, &start)
	if err != nil {
		return nil, err
	}
	size := TerminalSize{Cols: 80, Rows: 24}
	if options.Terminal != nil {
		size = *options.Terminal
	}
	if sizeErr := processTerminal(size); sizeErr != nil {
		return nil, sizeErr
	}
	body.Kind, body.Terminal = ProcessKindPTY, &size
	if body.Env == nil {
		body.Env = make(map[string]string)
	}
	if _, specified := body.Env["TERM"]; !specified {
		body.Env["TERM"] = "xterm-256color"
	}
	return s.sandbox.Process().start(ctx, body, 0)
}

// Create starts a terminal and attaches to it. The context bounds the returned
// connection's lifetime. If attaching fails, Create attempts to delete the
// process with a separate, at-most-five-second cleanup budget and returns the
// original attach error.
func (s *PTY) Create(ctx context.Context, command string, opts *PTYOptions) (*PTYConnection, error) {
	handle, err := s.Start(ctx, command, opts)
	if err != nil {
		return nil, err
	}
	connection, err := handle.Attach(ctx)
	if err != nil {
		cleanupCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
		defer cancel()
		handle.Delete(cleanupCtx) //nolint:errcheck // Best-effort rollback preserves the original attach error.
		return nil, err
	}
	return connection, nil
}

// Connect attaches to an existing terminal by process ID or live alias.
// The context bounds the returned connection's lifetime.
func (s *PTY) Connect(ctx context.Context, id string) (*PTYConnection, error) {
	handle, err := s.sandbox.Process().Connect(id)
	if err != nil {
		return nil, err
	}
	return handle.Attach(ctx)
}

// List returns one envelope containing the sandbox's terminal processes.
func (s *PTY) List(ctx context.Context) (*Page[ProcessInfo], error) {
	return s.sandbox.Process().List(ctx, &ListProcessesOptions{Kind: ProcessKindPTY})
}

// Delete stops a terminal and deletes its record and logs. It checks the
// server's process kind first and returns ErrUnsupportedOperation for every
// non-PTY kind, including unknown future kinds, without sending a signal.
func (s *PTY) Delete(ctx context.Context, id string) error {
	handle, err := s.sandbox.Process().Connect(id)
	if err != nil {
		return err
	}
	info, err := handle.Get(ctx)
	if err != nil {
		return err
	}
	if info.Kind != ProcessKindPTY {
		return newSDKError(CodeUnsupportedOperation, 400, "PTY.Delete is only supported for pty processes")
	}
	if killErr := handle.Kill(ctx, nil); killErr != nil && !errors.Is(killErr, ErrProcessTerminal) {
		return killErr
	}
	return handle.Delete(ctx)
}
