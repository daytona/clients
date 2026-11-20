// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"maps"
)

// Session is a persistent POSIX shell. Commands execute in order and retain cwd,
// exported variables and functions. INT interrupts one command without ending the
// shell. Sessions survive pause/resume, not a sandbox stop/start.
type Session struct{ handle *ProcessHandle }

// SessionJSON is the credential-free, cross-SDK shell-session identity.
type SessionJSON struct {
	// SandboxID identifies the owning sandbox.
	SandboxID string `json:"sandboxId"`
	// SessionID identifies the persistent shell process.
	SessionID string `json:"sessionId"`
}

// CreateSessionOptions configures settings inherited by every session command.
type CreateSessionOptions struct {
	// Tag is inherited by the shell's commands; uses the process tag rules.
	Tag string
	// Cwd sets the initial directory.
	Cwd string
	// Env adds environment variables to the shell.
	Env map[string]string
	// Shell selects a POSIX shell (bash/sh/dash/zsh/ksh).
	Shell string
	// Login selects a login shell; nil uses the sandbox default.
	Login *bool
	// Name is an optional live alias.
	Name string
}

// CreateSession creates a persistent shell. Non-POSIX shells are refused by the server.
func (s *ProcessService) CreateSession(ctx context.Context, opts *CreateSessionOptions) (*Session, error) {
	options := CreateSessionOptions{}
	if opts != nil {
		options = *opts
	}
	if options.Tag != "" {
		if err := processTag(options.Tag); err != nil {
			return nil, err
		}
	}
	handle, err := s.start(ctx, processCreateRequest{
		Kind: ProcessKindSession, Tag: options.Tag, Cwd: options.Cwd, Env: maps.Clone(options.Env),
		Shell: options.Shell, Login: clonePointer(options.Login), Name: options.Name,
	}, 0)
	if err != nil {
		return nil, err
	}
	return &Session{handle: handle}, nil
}

// ConnectSession binds a shell session ID without a request.
func (s *ProcessService) ConnectSession(id string) (*Session, error) {
	handle, err := s.Connect(id)
	if err != nil {
		return nil, err
	}
	handle.kind = ProcessKindSession
	return &Session{handle: handle}, nil
}

// ConnectSessionIdentity reconnects a serialized shell, rejecting another sandbox.
func (s *ProcessService) ConnectSessionIdentity(identity SessionJSON) (*Session, error) {
	if identity.SandboxID != s.sandbox.ID() {
		return nil, badRequest("session belongs to a different sandbox")
	}
	return s.ConnectSession(identity.SessionID)
}

// ID returns the shell's process ID.
func (s *Session) ID() string { return s.handle.ID() }

// SandboxID returns the owning sandbox.
func (s *Session) SandboxID() string { return s.handle.SandboxID() }

// ToJSON returns a credential-free identity accepted by ConnectSessionIdentity.
func (s *Session) ToJSON() SessionJSON {
	return SessionJSON{SandboxID: s.SandboxID(), SessionID: s.ID()}
}

// Get reads live shell metadata, including its current working directory.
func (s *Session) Get(ctx context.Context) (*ProcessInfo, error) { return s.handle.Get(ctx) }

// Delete ends the shell and deletes it and all of its command records.
func (s *Session) Delete(ctx context.Context) error { return s.handle.Delete(ctx) }
