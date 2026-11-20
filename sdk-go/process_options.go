// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// ProcessListState selects a process-list state filter, not a process lifecycle state.
// The zero value omits the filter (all); unsupported values return ErrBadRequest.
type ProcessListState string

const (
	// ProcessListStateAll selects processes in every state.
	ProcessListStateAll ProcessListState = "all"
	// ProcessListStateRunning selects live processes.
	ProcessListStateRunning ProcessListState = "running"
	// ProcessListStateTerminal selects processes whose execution has ended.
	ProcessListStateTerminal ProcessListState = "terminal"
)

// ProcessStartOptions configures a background command; nil selects all defaults.
type ProcessStartOptions struct {
	// Name is an optional live alias.
	Name string
	// Tag groups commands; 1..128 ASCII letters, digits, '.', '_' or '-', excluding '.' and '..'.
	Tag string
	// Cwd is the working directory; empty uses the sandbox default.
	Cwd string
	// Env adds environment variables. Nil omits env; an empty non-nil map sends {}.
	Env map[string]string
	// User overrides the OS user.
	User string
	// Shell overrides automatic shell selection (bash, otherwise sh).
	Shell string
	// Login selects a login shell; nil leaves selection to the sandbox.
	Login *bool
	// Stdin defaults to closed. Use open for Write/CloseStdin.
	Stdin ProcessStdinMode
	// ExecutionTimeout kills the process tree on expiry. Zero or NoTimeout disables it.
	ExecutionTimeout time.Duration
	// KeepLogs defaults to on_exit_ttl.
	KeepLogs ProcessKeepLogs
}

// WaitProcessOptions bounds observation, not execution.
type WaitProcessOptions struct {
	// Timeout defaults to unlimited; WaitForever also waits until terminal.
	// Expiry returns reason timed_out without killing the process; ctx cancellation is an error.
	Timeout time.Duration
}

// KillProcessOptions selects a signal and optional escalation.
type KillProcessOptions struct {
	// Signal defaults to TERM and must be a bare name, not SIGTERM.
	Signal ProcessSignal
	// EscalateAfter sends EscalateTo after this delay; nil disables escalation.
	// A pointer permits explicit immediate escalation (zero).
	EscalateAfter *time.Duration
	// EscalateTo defaults to KILL when EscalateAfter is set.
	EscalateTo ProcessSignal
}

// KillAllProcessesOptions bounds how long a tag-wide kill waits.
type KillAllProcessesOptions struct {
	// Timeout, when non-zero, is positive and at most 60s, rounded up to milliseconds.
	// Zero keeps the server's 10s default; no unbounded sentinel is accepted.
	Timeout time.Duration
}

// ListProcessesOptions filters the one-shot process list (not paginated by the server).
type ListProcessesOptions struct {
	// Tag selects only matching processes, even with older servers that ignore it.
	Tag string
	// State is running, terminal, or all; empty selects all.
	State ProcessListState
	// Kind filters by execution backend.
	Kind ProcessKind
	// SessionID filters by shell session or interpreter context.
	SessionID string
	// Name filters by live alias.
	Name string
	// PID filters by OS process ID; nil omits it.
	PID *int
}
