// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// PTYOptions configures a detached or connected terminal. Nil selects defaults.
type PTYOptions struct {
	// Cwd is the working directory. Empty starts in the user's home directory
	// when it is available (not with an explicit User), otherwise the sandbox's
	// working directory.
	Cwd string
	// Env adds environment variables. Nil omits env and an empty non-nil map
	// sends {}, before PTY defaults are applied: both still add
	// TERM=xterm-256color unless explicitly set.
	Env map[string]string
	// Name is an optional live alias.
	Name string
	// Tag groups processes; it must be a safe 1..128-character identifier.
	Tag string
	// User overrides the sandbox's OS user.
	User string
	// Shell overrides automatic shell selection.
	Shell string
	// Login selects a login shell; nil leaves selection to the sandbox.
	Login *bool
	// Stdin defaults to ProcessStdinOpen.
	Stdin ProcessStdinMode
	// ExecutionTimeout is a server-side deadline. Zero, NoTimeout, or a value
	// above the longest timer every SDK shares (about 24.8 days) means no deadline.
	ExecutionTimeout time.Duration
	// KeepLogs defaults to ProcessKeepLogsUntilCleanup.
	KeepLogs ProcessKeepLogs
	// Terminal defaults to 80 columns and 24 rows. Each dimension is 1..65535;
	// Windows sandboxes accept at most 32767.
	Terminal *TerminalSize
}
