// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

// ProcessKind identifies the execution backend.
type ProcessKind string

const (
	// ProcessKindUnknown preserves future kinds in RawKind.
	ProcessKindUnknown ProcessKind = "unknown"
	// ProcessKindExec is a command.
	ProcessKindExec ProcessKind = "exec"
	// ProcessKindPTY is an interactive terminal.
	ProcessKindPTY ProcessKind = "pty"
	// ProcessKindCode is a kernel-backed code cell.
	ProcessKindCode ProcessKind = "code"
	// ProcessKindSession is a persistent shell.
	ProcessKindSession ProcessKind = "session"
)

// ProcessState is the last observed execution state.
type ProcessState string

const (
	// ProcessStateUnknown preserves future states in RawState.
	ProcessStateUnknown ProcessState = "unknown"
	// ProcessStateStarting means execution is queued or starting.
	ProcessStateStarting ProcessState = "starting"
	// ProcessStateProvisioning means the kernel is being prepared.
	ProcessStateProvisioning ProcessState = "provisioning"
	// ProcessStateRunning means execution has begun.
	ProcessStateRunning ProcessState = "running"
	// ProcessStateTerminal means execution has ended.
	ProcessStateTerminal ProcessState = "terminal"
)

// ProcessReason reports why execution or a wait ended.
type ProcessReason string

const (
	// ProcessReasonUnknown means absent or unrecognized; see RawReason.
	ProcessReasonUnknown ProcessReason = "unknown"
	// ProcessReasonExited means the command exited normally, not necessarily successfully.
	ProcessReasonExited ProcessReason = "exited"
	// ProcessReasonSignaled means a signal ended execution.
	ProcessReasonSignaled ProcessReason = "signaled"
	// ProcessReasonTimedOut means the wait or execution deadline expired.
	ProcessReasonTimedOut ProcessReason = "timed_out"
	// ProcessReasonLost means execution was lost.
	ProcessReasonLost ProcessReason = "lost"
	// ProcessReasonFailed means execution failed.
	ProcessReasonFailed ProcessReason = "failed"
)

// ProcessStdinMode selects whether a process accepts input.
type ProcessStdinMode string

const (
	// ProcessStdinUnknown preserves future stdin modes in RawStdin.
	ProcessStdinUnknown ProcessStdinMode = "unknown"
	// ProcessStdinClosed starts with stdin closed (the exec default).
	ProcessStdinClosed ProcessStdinMode = "closed"
	// ProcessStdinOpen enables Write and CloseStdin.
	ProcessStdinOpen ProcessStdinMode = "open"
)

// ProcessKeepLogs selects process record and log retention.
type ProcessKeepLogs string

const (
	// ProcessKeepLogsUnknown preserves future policies in RawKeepLogs.
	ProcessKeepLogsUnknown ProcessKeepLogs = "unknown"
	// ProcessKeepLogsOnExitTTL retains output for a while after exit (the exec default).
	ProcessKeepLogsOnExitTTL ProcessKeepLogs = "on_exit_ttl"
	// ProcessKeepLogsUntilCleanup retains output until Delete.
	ProcessKeepLogsUntilCleanup ProcessKeepLogs = "until_cleanup"
	// ProcessKeepLogsNone disables logs; blocking runs refuse this policy.
	ProcessKeepLogsNone ProcessKeepLogs = "none"
)

// ProcessSignal names a signal without the SIG prefix.
type ProcessSignal string

const (
	// ProcessSignalUnknown preserves future signals in RawSignal.
	ProcessSignalUnknown ProcessSignal = "unknown"
	// ProcessSignalTerminate requests graceful termination.
	ProcessSignalTerminate ProcessSignal = "TERM"
	// ProcessSignalKill forces termination.
	ProcessSignalKill ProcessSignal = "KILL"
	// ProcessSignalInterrupt interrupts a command or code cell.
	ProcessSignalInterrupt ProcessSignal = "INT"
	// ProcessSignalHangup sends a hangup.
	ProcessSignalHangup ProcessSignal = "HUP"
	// ProcessSignalQuit sends a quit signal.
	ProcessSignalQuit ProcessSignal = "QUIT"
	// ProcessSignalUser1 sends the first user-defined signal.
	ProcessSignalUser1 ProcessSignal = "USR1"
	// ProcessSignalUser2 sends the second user-defined signal.
	ProcessSignalUser2 ProcessSignal = "USR2"
)

func processKind(raw string) ProcessKind {
	switch raw {
	case "exec":
		return ProcessKindExec
	case "pty":
		return ProcessKindPTY
	case "code":
		return ProcessKindCode
	case "session":
		return ProcessKindSession
	default:
		return ProcessKindUnknown
	}
}

func processState(raw string) ProcessState {
	switch raw {
	case "starting":
		return ProcessStateStarting
	case "provisioning":
		return ProcessStateProvisioning
	case "running":
		return ProcessStateRunning
	case "terminal":
		return ProcessStateTerminal
	default:
		return ProcessStateUnknown
	}
}

func processReason(raw string) ProcessReason {
	switch raw {
	case "exited":
		return ProcessReasonExited
	case "signaled":
		return ProcessReasonSignaled
	case "timed_out":
		return ProcessReasonTimedOut
	case "lost":
		return ProcessReasonLost
	case "failed":
		return ProcessReasonFailed
	default:
		return ProcessReasonUnknown
	}
}

func processStdin(raw string) ProcessStdinMode {
	switch raw {
	case "open":
		return ProcessStdinOpen
	case "closed":
		return ProcessStdinClosed
	default:
		return ProcessStdinUnknown
	}
}

func processRetention(raw string) ProcessKeepLogs {
	switch raw {
	case "on_exit_ttl":
		return ProcessKeepLogsOnExitTTL
	case "until_cleanup":
		return ProcessKeepLogsUntilCleanup
	case "none":
		return ProcessKeepLogsNone
	default:
		return ProcessKeepLogsUnknown
	}
}

func processSignal(raw string) ProcessSignal {
	switch raw {
	case "":
		return ""
	case "TERM":
		return ProcessSignalTerminate
	case "KILL":
		return ProcessSignalKill
	case "INT":
		return ProcessSignalInterrupt
	case "HUP":
		return ProcessSignalHangup
	case "QUIT":
		return ProcessSignalQuit
	case "USR1":
		return ProcessSignalUser1
	case "USR2":
		return ProcessSignalUser2
	default:
		return ProcessSignalUnknown
	}
}
