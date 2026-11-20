// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

// CodeBuildLogInterrupted indicates a locally interrupted build-log observation.
const CodeBuildLogInterrupted = "BUILD_LOG_INTERRUPTED"

// ErrBuildLogInterrupted matches a build-log observation interrupted before completion.
var ErrBuildLogInterrupted = codeSentinel{code: CodeBuildLogInterrupted}

// ErrTransferCancelled matches an explicitly cancelled file transfer.
var ErrTransferCancelled = codeSentinel{code: CodeRequestCancelled, status: 499}

// BuildLogInterruptedError preserves the last successfully delivered checkpoint.
type BuildLogInterruptedError struct {
	// SDKError is the underlying observation failure.
	SDKError *Error
	// Cursor is the last successfully delivered checkpoint, if any.
	Cursor string
}

// Error describes the interrupted observation.
func (e *BuildLogInterruptedError) Error() string { return e.SDKError.Error() }

// Unwrap exposes the underlying SDK error.
func (e *BuildLogInterruptedError) Unwrap() error { return e.SDKError }
