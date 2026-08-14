// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package options

import toolbox "github.com/daytona/clients/toolbox-api-client-go"

// ProcessStart configures a process launched by Start or Run. Start uses the
// lifecycle fields; Run additionally uses wait and output callback fields.
type ProcessStart struct {
	Argv          []string                     // Direct executable and arguments; alternative to ShellCommand.
	ShellCommand  *string                      // Command interpreted by a shell; alternative to Argv.
	Shell         *string                      // Shell selector used for ShellCommand.
	Login         *bool                        // Whether to start the selected shell as a login shell.
	Name          *string                      // Optional process name used by List filters.
	SessionID     *string                      // Optional grouping identifier used by List filters.
	Cwd           *string                      // Initial working directory.
	Env           map[string]string            // Environment variables.
	User          *string                      // OS user that runs the process.
	Stdin         *string                      // Stdin mode, such as pipe or null.
	TimeoutMs     *int                         // Process execution timeout in milliseconds.
	Kind          *string                      // Process kind, typically exec or pty.
	Terminal      *toolbox.ProcessTerminalSize // PTY dimensions; ignored for exec processes.
	KeepLogs      *string                      // Log retention policy; Start retains until cleanup by default.
	WaitTimeoutMs *int                         // Run-only wait timeout in milliseconds.
	OnStdout      func(string)                 // Run-only callback for decoded stdout chunks.
	OnStderr      func(string)                 // Run-only callback for decoded stderr chunks.
}

// ProcessRun is the Run-specific name for ProcessStart options. Use its wait
// and callback options with Run; Start returns a handle for manual supervision.
type ProcessRun = ProcessStart

// WithProcessArgv executes a program directly with arguments. Use
// WithProcessShellCommand when shell parsing or expansion is required.
func WithProcessArgv(argv ...string) func(*ProcessStart) {
	return func(opts *ProcessStart) { opts.Argv = argv }
}

// WithProcessShellCommand executes command through a shell. Use WithProcessArgv
// for direct execution without shell parsing.
func WithProcessShellCommand(command string) func(*ProcessStart) {
	return func(opts *ProcessStart) { opts.ShellCommand = &command }
}

// WithProcessShell selects the shell used by WithProcessShellCommand. It has no
// effect on direct argv execution.
func WithProcessShell(shell string) func(*ProcessStart) {
	return func(opts *ProcessStart) { opts.Shell = &shell }
}

// WithProcessLogin controls login-shell behavior for shell commands. Leave it
// unset when the process should inherit normal non-login shell behavior.
func WithProcessLogin(login bool) func(*ProcessStart) {
	return func(opts *ProcessStart) { opts.Login = &login }
}

// WithProcessName assigns a name that can later be matched by WithProcessListName.
func WithProcessName(name string) func(*ProcessStart) {
	return func(opts *ProcessStart) { opts.Name = &name }
}

// WithProcessSessionID groups a process under a session ID for filtered listing.
func WithProcessSessionID(sessionID string) func(*ProcessStart) {
	return func(opts *ProcessStart) { opts.SessionID = &sessionID }
}

// WithProcessCwd sets the process working directory instead of the sandbox default.
func WithProcessCwd(cwd string) func(*ProcessStart) {
	return func(opts *ProcessStart) { opts.Cwd = &cwd }
}

// WithProcessEnv sets process environment variables in addition to daemon defaults.
func WithProcessEnv(env map[string]string) func(*ProcessStart) {
	return func(opts *ProcessStart) { opts.Env = env }
}

// WithProcessUser runs the process as a specific sandbox OS user.
func WithProcessUser(user string) func(*ProcessStart) {
	return func(opts *ProcessStart) { opts.User = &user }
}

// WithProcessStdin selects stdin behavior. Use a piped mode when calling
// ProcessHandle.Stdin; use PTY kind for interactive terminal attachment.
func WithProcessStdin(stdin string) func(*ProcessStart) {
	return func(opts *ProcessStart) { opts.Stdin = &stdin }
}

// WithProcessTimeout sets the process execution timeout. This differs from
// WithProcessWaitTimeout, which only bounds how long Run waits.
func WithProcessTimeout(timeoutMs int) func(*ProcessStart) {
	return func(opts *ProcessStart) { opts.TimeoutMs = &timeoutMs }
}

// WithProcessKind selects exec or pty behavior. Choose pty only when terminal
// resize or attachment is needed.
func WithProcessKind(kind string) func(*ProcessStart) {
	return func(opts *ProcessStart) { opts.Kind = &kind }
}

// WithProcessTerminal configures PTY dimensions and terminal type. It is for
// kind "pty" processes; exec processes do not have a terminal to resize.
func WithProcessTerminal(cols, rows int, term string) func(*ProcessStart) {
	return func(opts *ProcessStart) {
		terminal := toolbox.NewProcessTerminalSize()
		terminal.SetCols(int32(cols))
		terminal.SetRows(int32(rows))
		if term != "" {
			terminal.SetTerm(term)
		}
		opts.Terminal = terminal
	}
}

// WithProcessKeepLogs overrides log retention. Start retains logs until Cleanup
// by default, while Run defaults to the short on_exit_ttl policy.
func WithProcessKeepLogs(keepLogs string) func(*ProcessStart) {
	return func(opts *ProcessStart) { opts.KeepLogs = &keepLogs }
}

// WithProcessWaitTimeout bounds how long Run waits without changing the process's
// own execution timeout. Use WithProcessTimeout to terminate long execution.
func WithProcessWaitTimeout(timeoutMs int) func(*ProcessRun) {
	return func(opts *ProcessRun) { opts.WaitTimeoutMs = &timeoutMs }
}

// WithProcessStdout streams decoded stdout chunks during Run while Run still
// returns the complete collected stdout. Use Start and StreamLogs for manual supervision.
func WithProcessStdout(callback func(string)) func(*ProcessRun) {
	return func(opts *ProcessRun) { opts.OnStdout = callback }
}

// WithProcessStderr streams decoded stderr chunks during Run while Run still
// returns the complete collected stderr. Use Start and StreamLogs for manual supervision.
func WithProcessStderr(callback func(string)) func(*ProcessRun) {
	return func(opts *ProcessRun) { opts.OnStderr = callback }
}

// ProcessList configures process discovery. Use Get or Connect instead when the
// process ID is already known.
type ProcessList struct {
	State     *string // Process state filter.
	Kind      *string // Process kind filter.
	Name      *string // Exact process name filter.
	SessionID *string // Session grouping filter.
}

// WithProcessListState filters List by process state, including "all" when supported.
func WithProcessListState(state string) func(*ProcessList) {
	return func(opts *ProcessList) { opts.State = &state }
}

// WithProcessListKind filters List by exec or pty process kind.
func WithProcessListKind(kind string) func(*ProcessList) {
	return func(opts *ProcessList) { opts.Kind = &kind }
}

// WithProcessListName filters List by the name assigned with WithProcessName.
func WithProcessListName(name string) func(*ProcessList) {
	return func(opts *ProcessList) { opts.Name = &name }
}

// WithProcessListSessionID filters List by the process session grouping ID.
func WithProcessListSessionID(sessionID string) func(*ProcessList) {
	return func(opts *ProcessList) { opts.SessionID = &sessionID }
}
