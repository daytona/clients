// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"net"
	"strconv"
	"time"
)

// Error is a Daytona server or SDK failure. Code and Source are open vocabularies.
// StatusCode is zero when no HTTP status exists. Err preserves the underlying
// cause, including context cancellation. Match categories or codes with errors.Is.
type Error struct {
	// StatusCode is zero when no HTTP status applies.
	StatusCode int
	// Code is the open, machine-readable error code.
	Code string
	// Source identifies the error-producing component.
	Source string
	// Message is the server or SDK explanation.
	Message string
	// RequestID identifies the server request, when reported.
	RequestID string
	// TraceID identifies the server trace, when reported.
	TraceID string
	// RetryAfter is a retry hint, not an automatic retry policy.
	RetryAfter time.Duration
	// Err is the underlying cause, including context cancellation.
	Err error
	// Feature names an unavailable feature.
	Feature string
	// Entitlement names a required entitlement.
	Entitlement string
	// Replacement names the replacement for a removed resource.
	Replacement string
	// FirstAvailableCursor is the oldest retained cursor.
	FirstAvailableCursor string
	// Stated is the request's region.
	Stated string
	// Actual is the resolved region.
	Actual string
	// Region is an unrecognized region selector.
	Region string
	// Limit names an exhausted organization limit.
	Limit string
	// Current is reported usage; nil means absent.
	Current *int64
	// Max is the reported maximum; nil means absent.
	Max *int64
	// MaxBytes is the allowed size in bytes; nil means absent.
	MaxBytes *int64
	// Bytes is the actual size in bytes; nil means absent.
	Bytes    *int64
	category errorCategory
}

// Error returns the server's message, or an HTTP/code fallback.
func (e *Error) Error() string {
	if e.Message != "" {
		return e.Message
	}
	if e.Code != "" {
		return e.Code
	}
	return "HTTP " + strconv.Itoa(e.StatusCode)
}

// Unwrap exposes the original cause.
func (e *Error) Unwrap() error { return e.Err }

// Is matches code sentinels and the category selected by source/code, then status.
func (e *Error) Is(target error) bool {
	switch match := target.(type) { // Targets are SDK sentinels, not wrapped failures.
	case codeSentinel:
		return e.Code == match.code
	case errorCategory:
		category := e.category
		if category == "" {
			status := e.StatusCode
			if known := catalogStatus(e.Source, e.Code); known != 0 {
				status = known
			}
			category = statusCategory(status)
		}
		return category == match || (category == ErrNotSupported && match == ErrInternalServer) ||
			(category == ErrClientTimeout && match == ErrTimeout)
	default:
		return false
	}
}

func newSDKError(code string, status int, message string) *Error {
	return &Error{Code: code, StatusCode: status, Source: "SDK", Message: message}
}

func badRequest(message string) *Error { return newSDKError("BAD_REQUEST", 400, message) }

func closedError() *Error { return newSDKError("CLIENT_CLOSED", 0, "Daytona client is closed") }

func wrapError(err error, message string) *Error {
	return &Error{Message: message, Source: "SDK", Err: err}
}

func transportError(ctx context.Context, err error) error {
	if errors.Is(context.Cause(ctx), ErrClientClosed) {
		return closedError()
	}
	if errors.Is(context.Cause(ctx), context.DeadlineExceeded) {
		err = context.DeadlineExceeded
	} else if ctx.Err() != nil {
		err = ctx.Err()
	}
	var sdkError *Error
	if errors.As(err, &sdkError) {
		return err
	}
	if errors.Is(err, context.Canceled) {
		return wrapError(err, "operation cancelled")
	}
	var network net.Error
	if errors.Is(err, context.DeadlineExceeded) || (errors.As(err, &network) && network.Timeout()) {
		result := newSDKError("REQUEST_TIMEOUT", 408, "operation timed out")
		result.Err = err
		result.category = ErrClientTimeout
		return result
	}
	return &Error{Source: "SDK", Message: err.Error(), Err: err, category: ErrConnection}
}

type codeSentinel struct {
	code   string
	status int
}

func (e codeSentinel) Error() string { return e.code }
func (e codeSentinel) Is(target error) bool {
	category, ok := target.(errorCategory)
	own := statusCategory(e.status)
	return ok && (category == own || (own == ErrNotSupported && category == ErrInternalServer))
}

type errorCategory string

func (e errorCategory) Error() string { return string(e) }

//nolint:gocyclo // This is a flat HTTP status catalog, not branching workflow logic.
func statusCategory(status int) errorCategory {
	switch status {
	case 400:
		return ErrBadRequest
	case 401:
		return ErrAuthentication
	case 403:
		return ErrForbidden
	case 404:
		return ErrNotFound
	case 405:
		return ErrMethodNotAllowed
	case 408:
		return ErrTimeout
	case 409:
		return ErrConflict
	case 410:
		return ErrGone
	case 412:
		return ErrPreconditionFailed
	case 413:
		return ErrPayloadTooLarge
	case 415:
		return ErrUnsupportedMediaType
	case 422:
		return ErrUnprocessableEntity
	case 428:
		return ErrPreconditionRequired
	case 429:
		return ErrRateLimit
	case 501:
		return ErrNotSupported
	case 503:
		return ErrServiceUnavailable
	default:
		if status >= 500 {
			return ErrInternalServer
		}
		return ""
	}
}
