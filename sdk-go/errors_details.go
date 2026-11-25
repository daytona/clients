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

// Error describes the failed wait, even when SDKError is nil.
func (e *SandboxTerminalStateError) Error() string {
	if e.SDKError == nil {
		return "daytona: sandbox reached a terminal state"
	}
	return e.SDKError.Error()
}

// Unwrap exposes the conflict error, or nil when absent.
func (e *SandboxTerminalStateError) Unwrap() error {
	if e.SDKError == nil {
		return nil
	}
	return e.SDKError
}

// ImageBuildFailedError names a failed image and its recorded build error.
type ImageBuildFailedError struct {
	// SDKError is the underlying build failure.
	SDKError *Error
	// ImageID identifies the failed image, when known.
	ImageID string
	// BuildError is the build's recorded failure detail.
	BuildError string
}

// Error describes the build failure, even when SDKError is nil.
func (e *ImageBuildFailedError) Error() string {
	if e.SDKError == nil {
		return "daytona: image build failed"
	}
	return e.SDKError.Error()
}

// Unwrap exposes the IMAGE_BUILD_FAILED error, or nil when absent.
func (e *ImageBuildFailedError) Unwrap() error {
	if e.SDKError == nil {
		return nil
	}
	return e.SDKError
}

// ImageBuildTimeoutError reports a build the server accepted that did not
// finish within the caller's build budget (BuildAndWait's Timeout,
// CreateFromBuild's BuildTimeout). The build keeps running on the server;
// ImageID lets the caller follow it (Images().FollowBuildLogs), wait on it
// (Images().Get with Wait) or delete it. It unwraps to the REQUEST_TIMEOUT
// error, so errors.Is(err, ErrRequestTimeout) still holds.
type ImageBuildTimeoutError struct {
	// SDKError is the underlying REQUEST_TIMEOUT error.
	SDKError *Error
	// ImageID identifies the still-building image.
	ImageID string
}

// Error describes the timed-out build, even when SDKError is nil.
func (e *ImageBuildTimeoutError) Error() string {
	if e.SDKError == nil {
		return "daytona: image build did not finish before timeout"
	}
	return e.SDKError.Error()
}

// Unwrap exposes the REQUEST_TIMEOUT error, or nil when absent.
func (e *ImageBuildTimeoutError) Unwrap() error {
	if e.SDKError == nil {
		return nil
	}
	return e.SDKError
}

// SandboxCreateRequestTimeoutError preserves the key needed to reconcile a
// create whose response timed out, including a caller context deadline.
// Retry with identical options and this key.
type SandboxCreateRequestTimeoutError struct {
	// SDKError is the underlying timeout error.
	SDKError *Error
	// IdempotencyKey must be reused with identical options to reconcile.
	IdempotencyKey string
}

// Error describes the uncertain create outcome, even when SDKError is nil.
func (e *SandboxCreateRequestTimeoutError) Error() string {
	if e.SDKError == nil {
		return "daytona: sandbox create request timed out"
	}
	return e.SDKError.Error()
}

// Unwrap exposes the client timeout, or nil when absent.
func (e *SandboxCreateRequestTimeoutError) Unwrap() error {
	if e.SDKError == nil {
		return nil
	}
	return e.SDKError
}

// SandboxCreateConnectionError preserves the key needed to reconcile a create
// whose response was lost, including caller cancellation or client closure
// during the request. It can also report a failure before the POST was sent,
// such as bearer exchange failure or client closure. Retrying with identical
// options and this key is safe in either case.
type SandboxCreateConnectionError struct {
	// SDKError is the underlying connection error.
	SDKError *Error
	// IdempotencyKey must be reused with identical options to reconcile.
	IdempotencyKey string
}

// Error describes the uncertain create outcome, even when SDKError is nil.
func (e *SandboxCreateConnectionError) Error() string {
	if e.SDKError == nil {
		return "daytona: sandbox create connection failed"
	}
	return e.SDKError.Error()
}

// Unwrap exposes the connection failure, or nil when absent.
func (e *SandboxCreateConnectionError) Unwrap() error {
	if e.SDKError == nil {
		return nil
	}
	return e.SDKError
}
