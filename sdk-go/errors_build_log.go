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
// Every instance matches ErrBuildLogInterrupted through errors.Is; the cause's
// category and code remain reachable through Unwrap.
type BuildLogInterruptedError struct {
	// SDKError is the underlying observation failure.
	SDKError *Error
	// Cursor is the last successfully delivered checkpoint, if any.
	Cursor string
}

// Error describes the interrupted observation.
func (e *BuildLogInterruptedError) Error() string {
	if e.SDKError == nil {
		return "daytona: build log observation interrupted"
	}
	return e.SDKError.Error()
}

// Is matches ErrBuildLogInterrupted independently of the underlying cause.
func (e *BuildLogInterruptedError) Is(target error) bool {
	return target == error(ErrBuildLogInterrupted)
}

// Unwrap exposes the underlying SDK error.
func (e *BuildLogInterruptedError) Unwrap() error {
	if e.SDKError == nil {
		return nil
	}
	return e.SDKError
}
