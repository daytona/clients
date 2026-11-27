// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestErrorWrappersDescribeZeroValue(t *testing.T) {
	for _, wrapper := range sdkErrorWrappers() {
		t.Run(wrapper.name, func(t *testing.T) {
			// Given: a caller-constructed wrapper with no SDK error.
			err := wrapper.wrap(nil)
			// When
			var message string
			require.NotPanics(t, func() { message = err.Error() })
			// Then
			require.NotEmpty(t, message)
		})
	}
}

func TestErrorWrappersUnwrapNilSDKError(t *testing.T) {
	for _, wrapper := range sdkErrorWrappers() {
		t.Run(wrapper.name, func(t *testing.T) {
			// Given
			err := wrapper.wrap(nil)
			// When
			cause := errors.Unwrap(err)
			// Then: the chain ends at a nil interface, not a typed-nil error.
			require.NoError(t, cause)
			var detail *Error
			require.NotErrorAs(t, err, &detail)
			require.NotErrorIs(t, err, ErrBadRequest)
		})
	}
}

func TestErrorWrappersPreserveSDKError(t *testing.T) {
	for _, wrapper := range sdkErrorWrappers() {
		t.Run(wrapper.name, func(t *testing.T) {
			// Given
			cause := &Error{
				Message: "operation cancelled", Source: SourceSDK,
				Code: CodeRequestCancelled, StatusCode: 499, Err: context.Canceled,
			}
			// When
			err := wrapper.wrap(cause)
			// Then: formatting, identity, and the entire cause chain survive.
			require.Equal(t, cause.Error(), err.Error())
			require.Same(t, cause, errors.Unwrap(err))
			require.ErrorIs(t, err, ErrRequestCancelled)
			require.ErrorIs(t, err, context.Canceled)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Same(t, cause, detail)
		})
	}
}

func sdkErrorWrappers() []struct {
	name string
	wrap func(*Error) error
} {
	return []struct {
		name string
		wrap func(*Error) error
	}{
		{"SandboxTerminalStateError", func(e *Error) error {
			return &SandboxTerminalStateError{SDKError: e}
		}},
		{"ImageBuildFailedError", func(e *Error) error {
			return &ImageBuildFailedError{SDKError: e}
		}},
		{"ImageBuildTimeoutError", func(e *Error) error {
			return &ImageBuildTimeoutError{SDKError: e}
		}},
		{"SandboxCreateRequestTimeoutError", func(e *Error) error {
			return &SandboxCreateRequestTimeoutError{SDKError: e}
		}},
		{"SandboxCreateConnectionError", func(e *Error) error {
			return &SandboxCreateConnectionError{SDKError: e}
		}},
		{"DockerWaitTimeoutError", func(e *Error) error {
			return &DockerWaitTimeoutError{SDKError: e}
		}},
		{"DockerNotReadyError", func(e *Error) error {
			return &DockerNotReadyError{SDKError: e}
		}},
	}
}
