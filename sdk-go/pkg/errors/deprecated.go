// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package errors

import "net/http"

// =============================================================================
// Backward-compatibility layer. New functionality belongs in errors.go, not
// here. This file (and its test file) can be removed as a whole in a future
// major release.
// =============================================================================
//
// This file preserves the pre-sentinel typed error surface. The SDK itself
// only produces *DaytonaError; the named types below exist so that older
// callers using `errors.As(err, &typedErr)` keep working with the SAME
// matching semantics they had before (e.g. *DaytonaNotFoundError only
// matches HTTP 404), via the As hook implemented on *DaytonaError.
//
// Note: direct type switches (`case *errors.DaytonaNotFoundError:`) on
// SDK-returned errors do not match anymore — use errors.As or errors.Is.

// Deprecated: match with `errors.Is(err, ErrNotFound)` instead.
type DaytonaNotFoundError struct{ *DaytonaError }

// Deprecated: match with `errors.Is(err, ErrRateLimit)` instead.
type DaytonaRateLimitError struct{ *DaytonaError }

// Deprecated: match with `errors.Is(err, ErrAuthentication)` instead.
type DaytonaAuthenticationError struct{ *DaytonaError }

// Deprecated: match with `errors.Is(err, ErrForbidden)` instead.
type DaytonaForbiddenError struct{ *DaytonaError }

// Deprecated: match with `errors.Is(err, ErrConflict)` instead.
type DaytonaConflictError struct{ *DaytonaError }

// Deprecated: match with `errors.Is(err, ErrBadRequest)` instead.
type DaytonaValidationError struct{ *DaytonaError }

// Deprecated: match with `errors.Is(err, ErrInternalServer)` or compare
// StatusCode >= 500 on *DaytonaError instead.
type DaytonaServerError struct{ *DaytonaError }

// Deprecated: match with `errors.Is(err, ErrTimeout)` or
// `errors.Is(err, ErrGatewayTimeout)` instead.
type DaytonaTimeoutError struct{ *DaytonaError }

func (e *DaytonaNotFoundError) Unwrap() error       { return e.DaytonaError }
func (e *DaytonaRateLimitError) Unwrap() error      { return e.DaytonaError }
func (e *DaytonaAuthenticationError) Unwrap() error { return e.DaytonaError }
func (e *DaytonaForbiddenError) Unwrap() error      { return e.DaytonaError }
func (e *DaytonaConflictError) Unwrap() error       { return e.DaytonaError }
func (e *DaytonaValidationError) Unwrap() error     { return e.DaytonaError }
func (e *DaytonaServerError) Unwrap() error         { return e.DaytonaError }
func (e *DaytonaTimeoutError) Unwrap() error        { return e.DaytonaError }

// As lets `errors.As` populate the deprecated typed errors with their
// original status-code semantics, even though the SDK only produces
// *DaytonaError. Runs only after the stdlib's direct assignability check,
// so `errors.As(err, &de)` with a *DaytonaError target is unaffected.
func (e *DaytonaError) As(target any) bool {
	switch t := target.(type) {
	case **DaytonaNotFoundError:
		if e.StatusCode == http.StatusNotFound {
			*t = &DaytonaNotFoundError{e}
			return true
		}
	case **DaytonaRateLimitError:
		if e.StatusCode == http.StatusTooManyRequests {
			*t = &DaytonaRateLimitError{e}
			return true
		}
	case **DaytonaAuthenticationError:
		if e.StatusCode == http.StatusUnauthorized {
			*t = &DaytonaAuthenticationError{e}
			return true
		}
	case **DaytonaForbiddenError:
		if e.StatusCode == http.StatusForbidden {
			*t = &DaytonaForbiddenError{e}
			return true
		}
	case **DaytonaConflictError:
		if e.StatusCode == http.StatusConflict {
			*t = &DaytonaConflictError{e}
			return true
		}
	case **DaytonaValidationError:
		if e.StatusCode == http.StatusBadRequest {
			*t = &DaytonaValidationError{e}
			return true
		}
	case **DaytonaServerError:
		if e.StatusCode >= 500 && e.StatusCode <= 599 {
			*t = &DaytonaServerError{e}
			return true
		}
	case **DaytonaTimeoutError:
		if e.StatusCode == http.StatusRequestTimeout || e.StatusCode == http.StatusGatewayTimeout {
			*t = &DaytonaTimeoutError{e}
			return true
		}
	}
	return false
}

// Deprecated: use NewDaytonaError(message, http.StatusNotFound, headers).
func NewDaytonaNotFoundError(message string, headers http.Header) *DaytonaNotFoundError {
	return &DaytonaNotFoundError{NewDaytonaError(message, http.StatusNotFound, headers)}
}

// Deprecated: use NewDaytonaError(message, http.StatusTooManyRequests, headers).
func NewDaytonaRateLimitError(message string, headers http.Header) *DaytonaRateLimitError {
	return &DaytonaRateLimitError{NewDaytonaError(message, http.StatusTooManyRequests, headers)}
}

// Deprecated: use NewDaytonaError(message, http.StatusUnauthorized, headers).
func NewDaytonaAuthenticationError(message string, headers http.Header) *DaytonaAuthenticationError {
	return &DaytonaAuthenticationError{NewDaytonaError(message, http.StatusUnauthorized, headers)}
}

// Deprecated: use NewDaytonaError(message, http.StatusForbidden, headers).
func NewDaytonaForbiddenError(message string, headers http.Header) *DaytonaForbiddenError {
	return &DaytonaForbiddenError{NewDaytonaError(message, http.StatusForbidden, headers)}
}

// Deprecated: use NewDaytonaError(message, http.StatusConflict, headers).
func NewDaytonaConflictError(message string, headers http.Header) *DaytonaConflictError {
	return &DaytonaConflictError{NewDaytonaError(message, http.StatusConflict, headers)}
}

// Deprecated: use NewDaytonaError(message, statusCode, headers).
func NewDaytonaServerError(message string, statusCode int, headers http.Header) *DaytonaServerError {
	return &DaytonaServerError{NewDaytonaError(message, statusCode, headers)}
}
