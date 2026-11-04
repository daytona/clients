// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

// Package cerr defines the CLI's exit-code vocabulary and
// the error type every command returns. main() maps the error to an exit
// code and a one-line stderr message with an actionable hint.
package cerr

import (
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

// Error is a CLI error with an exit code and an optional hint line.
type Error struct {
	Code int
	Msg  string
	Hint string
	Err  error
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
		WithHint("pass --yes to confirm, or set DAYTONA_YES=1")
}

// MissingArg is the non-interactive "would have shown a picker" refusal.
func MissingArg(what, flag string) *Error {
	return New(ExitUsage, "%s is required in non-interactive mode", what).
		WithHint("pass %s, or run interactively to pick one", flag)
}
