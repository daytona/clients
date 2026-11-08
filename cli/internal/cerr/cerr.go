// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

// Package cerr defines the CLI's exit-code vocabulary and
// the error type every command returns. main() maps the error to an exit
// code and a one-line stderr message with an actionable hint.
package cerr

import (
	"context"
	"errors"
	"fmt"
	"net/http"
)

// Exit codes. Stable: scripts and agents branch on them.
const (
	ExitOK          = 0
	ExitFailure     = 1
	ExitUsage       = 2 // bad flags, invalid --file, missing --yes / argument off-TTY
	ExitAuth        = 3 // 401, no credential, expired and unrefreshable
	ExitForbidden   = 4 // 403 — stderr names the missing permission + credential kind
	ExitNotFound    = 5 // 404
	ExitConflict    = 6 // 409, class mismatch, wrong API endpoint (501)
	ExitPartial     = 7 // some regions/items failed; successful rows retained
	ExitTimeout     = 8 // wait timed out; server-side work continues
	ExitInterrupted = 130
)

// Sources of errors the CLI reports itself. Server errors carry the
// source the server named (DAYTONA_API, DAYTONA_DAEMON, ...).
const (
	SourceCLI     = "CLI"     // a local error: usage, configuration, a local wait deadline
	SourceProcess = "PROCESS" // a sandbox process ended unsuccessfully; its status passed through
	SourceSSH     = "SSH"     // the system ssh client's exit status passed through
)

// Error is a CLI error with an exit code and an optional hint line.
type Error struct {
	Code int
	Msg  string
	Hint string
	Err  error
	// Source overrides the reported error source (see Details); empty
	// means "the wrapped error's source, else CLI".
	Source string
}

func (e *Error) Error() string {
	if e.Err != nil && e.Msg == "" {
		return e.Err.Error()
	}
	return e.Msg
}

func (e *Error) Unwrap() error { return e.Err }

// New builds an Error.
func New(code int, format string, a ...any) *Error {
	return &Error{Code: code, Msg: fmt.Sprintf(format, a...)}
}

// WithHint attaches a hint line (printed after the message on stderr).
func (e *Error) WithHint(format string, a ...any) *Error {
	e.Hint = fmt.Sprintf(format, a...)
	return e
}

// WithSource sets the reported error source.
func (e *Error) WithSource(source string) *Error {
	e.Source = source
	return e
}

// Detailed is implemented by errors that carry the server's machine-readable
// error metadata (the API and toolbox error envelopes).
type Detailed interface {
	error
	ErrorCode() string
	ErrorSource() string
	ErrorTraceID() string
}

// Details is the machine-readable form of an error, printed on stderr as
// {"error": {...}} in JSON output mode. Empty fields are omitted.
type Details struct {
	ExitCode int    `json:"exit_code"`
	Code     string `json:"code,omitempty"`
	Message  string `json:"message"`
	Source   string `json:"source,omitempty"`
	TraceID  string `json:"trace_id,omitempty"`
	Hint     string `json:"hint,omitempty"`
}

// DetailsOf collects the exit code, message, hint and any server metadata
// of err. The source is, in order: one set with WithSource; the server's
// (omitted when a server error names none, so it is never reported as the
// CLI's); CLI for a local error.
func DetailsOf(err error) Details {
	d := Details{ExitCode: ExitCode(err), Message: err.Error()}
	var de Detailed
	if errors.As(err, &de) {
		d.Code = de.ErrorCode()
		d.Source = de.ErrorSource()
		d.TraceID = de.ErrorTraceID()
	} else {
		d.Source = SourceCLI
	}
	var ce *Error
	if errors.As(err, &ce) {
		d.Hint = ce.Hint
		if ce.Source != "" {
			d.Source = ce.Source
		}
	}
	return d
}

// Usage is a usage/validation error (exit 2).
func Usage(format string, a ...any) *Error { return New(ExitUsage, format, a...) }

// Wrap wraps err with a code, keeping err for errors.Is/As.
func Wrap(code int, err error, format string, a ...any) *Error {
	return &Error{Code: code, Msg: fmt.Sprintf(format, a...), Err: err}
}

// ExitCode returns the exit code for any error (1 for unknown errors).
func ExitCode(err error) int {
	if err == nil {
		return ExitOK
	}
	var e *Error
	if errors.As(err, &e) {
		return e.Code
	}
	return ExitFailure
}

// FromContext reports the caller's own context ending, nil while ctx is live:
// a passed deadline is a timeout, anything else an interruption.
func FromContext(ctx context.Context) error {
	err := ctx.Err()
	switch {
	case err == nil:
		return nil
	case errors.Is(err, context.DeadlineExceeded):
		return Wrap(ExitTimeout, err, "timed out")
	default:
		return Wrap(ExitInterrupted, err, "interrupted")
	}
}

// CodeForStatus maps an HTTP status to an exit code.
func CodeForStatus(status int) int {
	switch {
	case status == http.StatusUnauthorized:
		return ExitAuth
	case status == http.StatusForbidden:
		return ExitForbidden
	case status == http.StatusNotFound:
		return ExitNotFound
	case status == http.StatusConflict, status == http.StatusNotImplemented, status == http.StatusPreconditionFailed:
		return ExitConflict
	case status == http.StatusBadRequest, status == http.StatusUnprocessableEntity:
		return ExitUsage
	case status == http.StatusRequestTimeout, status == http.StatusGatewayTimeout:
		return ExitTimeout
	default:
		return ExitFailure
	}
}

// RefuseWithoutYes is the non-interactive destructive-verb refusal.
func RefuseWithoutYes(verb, target string) *Error {
	return New(ExitUsage, "refusing to %s %s without --yes in non-interactive mode (or DAYTONA_YES=1)", verb, target).
		WithHint("in non-interactive mode (no terminal, -o json|yaml, or CI) pass --yes to confirm, or set DAYTONA_YES=1")
}

// MissingArg is the non-interactive "would have shown a picker" refusal.
func MissingArg(what, flag string) *Error {
	return New(ExitUsage, "%s is required in non-interactive mode", what).
		WithHint("pass %s, or run interactively to pick one", flag)
}
