// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// TerminalSize is a terminal's size in character cells; each dimension is 1..65535.
type TerminalSize struct {
	// Cols is the number of columns.
	Cols int `json:"cols"`
	// Rows is the number of rows.
	Rows int `json:"rows"`
}

// WaitResult reports only what the sandbox reported. A nil ExitCode means absent,
// never an assumed success. A caller wait timeout does not stop execution.
type WaitResult struct {
	// Reason is the normalized outcome; unknown when unreported.
	Reason ProcessReason
	// RawReason is the server's outcome verbatim, or empty when absent.
	RawReason string
	// ExitCode is nil when unreported; non-zero is not an SDK error.
	ExitCode *int
	// Signal is the normalized bare signal name, or empty when absent.
	Signal ProcessSignal
	// RawSignal is the server's signal without its SIG prefix.
	RawSignal string
}

// ProcessResult holds a command's terminal or timed-out result and retained output.
// A non-zero ExitCode is NOT an error. Err from Exec/Run indicates invalid input,
// a transport/protocol failure, cancellation, or a server error.
type ProcessResult struct {
	WaitResult
	// ProcessID can be passed to Process.Connect to continue observing a timed-out run.
	ProcessID string
	// Stdout contains stdout (and PTY) frames only.
	Stdout string
	// Stderr contains stderr frames only.
	Stderr string
	// Output interleaves stdout/stderr/PTY frames in log order, excluding setup narration.
	Output string
	// Logs contains every retained frame used to collect this result.
	Logs []LogFrame
}

// ProcessInfo is a caller-owned process metadata snapshot.
type ProcessInfo struct {
	WaitResult
	// ID is the stable process ID.
	ID string
	// Kind is the normalized execution backend.
	Kind ProcessKind
	// RawKind preserves the server's kind.
	RawKind string
	// State is the normalized execution state.
	State ProcessState
	// RawState preserves the server's state.
	RawState string
	// Cwd is the current working directory.
	Cwd string
	// Stdin is the normalized input mode.
	Stdin ProcessStdinMode
	// RawStdin preserves the server's input mode.
	RawStdin string
	// KeepLogs is the normalized retention policy.
	KeepLogs ProcessKeepLogs
	// RawKeepLogs preserves the server's retention policy.
	RawKeepLogs string
	// CreatedAt is the creation timestamp.
	CreatedAt time.Time
	// System marks protected system processes.
	System bool
	// Name is the optional live alias.
	Name string
	// Tag groups related processes; session commands inherit their shell's tag.
	Tag string
	// PID is the OS process ID, when reported.
	PID *int
	// SessionID is the owning shell or interpreter context.
	SessionID string
	// Argv is the shell-free argument vector, when reported.
	Argv []string
	// ShellCommand is the shell command, when reported.
	ShellCommand string
	// Code is the code cell, when reported.
	Code string
	// Language is the open interpreter language name.
	Language string
	// Shell is the requested shell.
	Shell string
	// ResolvedShell is the actual shell binary.
	ResolvedShell string
	// Login distinguishes absent from a reported false login-shell setting.
	Login *bool
	// User is the OS user.
	User string
	// ExecutionTimeout is the server execution deadline; nil means unreported.
	ExecutionTimeout *time.Duration
	// Terminal is the PTY size, when reported.
	Terminal *TerminalSize
	// StartedAt is zero until reported.
	StartedAt time.Time
	// ExitedAt is zero until reported.
	ExitedAt time.Time
	// TruncatedHead reports retention loss.
	TruncatedHead bool
	// FirstAvailableCursor identifies the oldest retained frame.
	FirstAvailableCursor string
	// LastCursor identifies the newest retained frame.
	LastCursor string
}
