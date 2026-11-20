// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

// SandboxTerminalStateError reports a terminal state reached before a wait target.
type SandboxTerminalStateError struct {
	// SDKError is the underlying conflict error.
	SDKError *Error
	// State is the terminal state observed.
	State SandboxState
	// RawState is the server status verbatim.
	RawState string
	// Reason is the server's explanation, when reported.
	Reason string
}

// Error describes the failed wait.
func (e *SandboxTerminalStateError) Error() string { return e.SDKError.Error() }

// Unwrap exposes the conflict error.
func (e *SandboxTerminalStateError) Unwrap() error { return e.SDKError }

// ImageBuildFailedError names a failed image and its recorded build error.
type ImageBuildFailedError struct {
	// SDKError is the underlying build failure.
	SDKError *Error
	// ImageID identifies the failed image, when known.
	ImageID string
	// BuildError is the build's recorded failure detail.
	BuildError string
}

// Error describes the build failure.
func (e *ImageBuildFailedError) Error() string { return e.SDKError.Error() }

// Unwrap exposes the IMAGE_BUILD_FAILED error.
func (e *ImageBuildFailedError) Unwrap() error { return e.SDKError }

// SandboxCreateRequestTimeoutError preserves the key needed to reconcile a
// create whose response timed out, including a caller context deadline.
// Retry with identical options and this key.
type SandboxCreateRequestTimeoutError struct {
	// SDKError is the underlying timeout error.
	SDKError *Error
	// IdempotencyKey must be reused with identical options to reconcile.
	IdempotencyKey string
}

// Error describes the uncertain create outcome.
func (e *SandboxCreateRequestTimeoutError) Error() string { return e.SDKError.Error() }

// Unwrap exposes the client timeout.
func (e *SandboxCreateRequestTimeoutError) Unwrap() error { return e.SDKError }

// SandboxCreateConnectionError preserves the key needed to reconcile a create
// whose response was lost, including caller cancellation during the request.
// Retry with identical options and this key.
type SandboxCreateConnectionError struct {
	// SDKError is the underlying connection error.
	SDKError *Error
	// IdempotencyKey must be reused with identical options to reconcile.
	IdempotencyKey string
}

// Error describes the uncertain create outcome.
func (e *SandboxCreateConnectionError) Error() string { return e.SDKError.Error() }

// Unwrap exposes the connection failure.
func (e *SandboxCreateConnectionError) Unwrap() error { return e.SDKError }
