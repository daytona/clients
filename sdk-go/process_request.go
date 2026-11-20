// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"maps"
	"slices"
	"strings"
	"time"
)

// processCreateRequest is the JSON boundary. Optional command pointers preserve
// an explicit empty command; command selection is enforced by the public methods.
type processCreateRequest struct {
	ShellCommand *string           `json:"shellCommand,omitempty"`
	Argv         []string          `json:"argv,omitempty"`
	Code         *string           `json:"code,omitempty"`
	Kind         ProcessKind       `json:"kind,omitempty"`
	Name         string            `json:"name,omitempty"`
	Tag          string            `json:"tag,omitempty"`
	SessionID    string            `json:"sessionId,omitempty"`
	Cwd          string            `json:"cwd,omitempty"`
	Env          map[string]string `json:"env,omitzero"`
	User         string            `json:"user,omitempty"`
	Shell        string            `json:"shell,omitempty"`
	Login        *bool             `json:"login,omitempty"`
	Stdin        ProcessStdinMode  `json:"stdin,omitempty"`
	TimeoutMs    int64             `json:"timeoutMs,omitempty"`
	Terminal     *TerminalSize     `json:"terminal,omitempty"`
	Language     string            `json:"language,omitempty"`
	KeepLogs     ProcessKeepLogs   `json:"keepLogs,omitempty"`
}

func processStartBody(command *string, argv []string, opts *ProcessStartOptions) (processCreateRequest, error) {
	if len(argv) > 0 && argv[0] == "" {
		return processCreateRequest{}, badRequest("argv executable must not be empty")
	}
	options := ProcessStartOptions{}
	if opts != nil {
		options = *opts
	}
	if options.Tag != "" {
		if err := processTag(options.Tag); err != nil {
			return processCreateRequest{}, err
		}
	}
	timeout, err := processTimeout("ExecutionTimeout", options.ExecutionTimeout)
	if err != nil {
		return processCreateRequest{}, err
	}
	stdin := options.Stdin
	if stdin == "" {
		stdin = ProcessStdinClosed
	}
	if processStdin(string(stdin)) == ProcessStdinUnknown {
		return processCreateRequest{}, badRequest("Stdin must be open or closed")
	}
	retention := options.KeepLogs
	if retention == "" {
		retention = ProcessKeepLogsOnExitTTL
	}
	if processRetention(string(retention)) == ProcessKeepLogsUnknown {
		return processCreateRequest{}, badRequest("KeepLogs must be on_exit_ttl, until_cleanup or none")
	}
	return processCreateRequest{
		ShellCommand: clonePointer(command), Argv: slices.Clone(argv), Kind: ProcessKindExec,
		Name: options.Name, Tag: options.Tag, Cwd: options.Cwd, Env: maps.Clone(options.Env),
		User: options.User, Shell: options.Shell, Login: clonePointer(options.Login),
		Stdin: stdin, TimeoutMs: wireMilliseconds(timeout), KeepLogs: retention,
	}, nil
}

func processTimeout(name string, value time.Duration) (time.Duration, error) {
	if value < 0 && value != WaitForever && value != NoTimeout {
		return 0, badRequest(name + " must be non-negative, WaitForever or NoTimeout")
	}
	return boundedTimeout(value), nil
}

func processTag(tag string) error {
	if len(tag) == 0 || len(tag) > 128 || tag == "." || tag == ".." {
		return badRequest("Tag must be 1..128 safe ASCII characters, excluding '.' and '..'")
	}
	for _, char := range tag {
		valid := char >= 'a' && char <= 'z' || char >= 'A' && char <= 'Z' ||
			char >= '0' && char <= '9' || strings.ContainsRune("._-", char)
		if !valid {
			return badRequest("Tag must contain only ASCII letters, digits, '.', '_' and '-'")
		}
	}
	return nil
}

func processTerminal(size TerminalSize) error {
	if size.Cols < 1 || size.Cols > 65535 || size.Rows < 1 || size.Rows > 65535 {
		return badRequest("Terminal Cols and Rows must each be between 1 and 65535")
	}
	return nil
}
