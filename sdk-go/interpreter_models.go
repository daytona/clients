// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"io"
	"time"
)

// InterpreterContextJSON is the cross-SDK, credential-free context identity.
type InterpreterContextJSON struct {
	// SandboxID identifies the sandbox owning the context.
	SandboxID string `json:"sandboxId"`
	// ContextID identifies the kernel context.
	ContextID string `json:"contextId"`
	// Language identifies the context's language.
	Language string `json:"language"`
}

// MarshalJSON serializes only the context's identity, without credentials.
func (c *InterpreterContext) MarshalJSON() ([]byte, error) {
	data, err := json.Marshal(c.Identity())
	if err != nil {
		return nil, wrapError(err, "encode interpreter context identity")
	}
	return data, nil
}

// CreateInterpreterContextOptions configures an independent kernel context.
type CreateInterpreterContextOptions struct {
	// ContextID optionally requests a specific ID; empty lets the server choose.
	ContextID string
	// Cwd sets the default working directory.
	Cwd string
	// Env sets the environment for every execution in the context.
	Env map[string]string
}

// InterpreterContextRunOptions configures execution in an existing context.
// Writer and callback failures return a partial result with Process.ProcessID
// and preserve the local error, with the same best-effort detached interrupt
// semantics as RunCodeOptions.
type InterpreterContextRunOptions struct {
	// Cwd overrides the context's working directory for this execution.
	Cwd string
	// Timeout bounds queueing, submission and execution. Zero waits indefinitely
	// but keeps the client's request timeout for submission. WaitForever and
	// NoTimeout also remove the submission timeout. Expiry interrupts the kernel.
	Timeout time.Duration
	// Stdout receives stdout as it arrives. Nil buffers output only.
	Stdout io.Writer
	// Stderr receives stderr as it arrives. Nil buffers output only.
	Stderr io.Writer
	// OnOutput receives rich display and execution results in log order.
	OnOutput func(InterpreterOutput) error
	// OnError receives interpreter errors in log order.
	OnError func(InterpreterError) error
	// OnLog receives every log frame, including setup and unknown channels.
	// Callback errors stop delivery and are wrapped as SDK errors.
	OnLog func(LogFrame) error
}

// InterpreterOutput is a rich MIME bundle emitted by a language kernel.
type InterpreterOutput struct {
	// Data preserves every MIME value as encoded JSON.
	Data map[string]JSONValue
	// IsMainResult is true only for execute_result frames.
	IsMainResult bool
	// Text is text/plain, when the value is a string.
	Text *string
	// HTML is text/html, when the value is a string.
	HTML *string
	// PNG is image/png, usually a base64-encoded string.
	PNG *string
	// SVG is image/svg+xml, when the value is a string.
	SVG *string
	// JSON is application/json as encoded JSON; nil means absent.
	JSON JSONValue
}

// InterpreterError describes an exception reported by the language kernel.
// It is part of a successful RunCodeResult, not a transport error.
type InterpreterError struct {
	// Name is the exception class name.
	Name string
	// Value is the exception message.
	Value string
	// Traceback contains the kernel's formatted stack trace.
	Traceback []string
}

// InterpreterLogs contains only stdout and stderr, excluding setup narration.
type InterpreterLogs struct {
	// Stdout concatenates stdout frames in order.
	Stdout string
	// Stderr concatenates stderr frames in order.
	Stderr string
}

// InterpreterProcess records the server's execution outcome.
type InterpreterProcess struct {
	WaitResult
	// ProcessID is empty when the caller budget expired before submission.
	ProcessID string
}

// RunCodeResult contains a code execution's outcome and decomposed outputs.
// Reconnected process handles support replay/control, not reconstruction of this result.
type RunCodeResult struct {
	// Process contains the process identity and unmodified wait outcome.
	Process InterpreterProcess
	// Results contains rich display and expression outputs in log order.
	Results []InterpreterOutput
	// Logs contains channel-filtered stdout and stderr.
	Logs InterpreterLogs
	// Error is the last kernel exception, or nil when none was reported.
	Error *InterpreterError
}
