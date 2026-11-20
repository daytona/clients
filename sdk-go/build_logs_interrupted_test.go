// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"errors"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestBuildLogsInterruptionMatchesSentinelAndCause(t *testing.T) {
	for _, tt := range []struct {
		name string
		body string
		code string
	}{
		{"protocol", "event: state\ndata: {}\n\n", "BUILD_LOG_PROTOCOL_ERROR"},
		{"source changed", "event: source_changed\ndata: {\"reason\":\"replacement\"}\n\n", "BUILD_LOG_SOURCE_CHANGED"},
		{"non-retryable error", "event: error\ndata: {\"error\":\"refused\",\"code\":\"UNAUTHORIZED\",\"retryable\":false}\n\n", CodeUnauthorized},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given a delivered checkpoint followed by an interruption.
			fake := buildLogsFake(t, buildLogsSSE(t, buildLogsChunk("a", 0)+tt.body))
			client := newTestClient(t, fake.Server)
			// When consuming the public iterator.
			_, err := buildLogsCollect(client.Images().FollowBuildLogs(t.Context(), "image", nil))
			// Then interruption identity is independent of the cause's code.
			require.ErrorIs(t, err, ErrBuildLogInterrupted)
			require.ErrorIs(t, err, codeSentinel{code: tt.code})
			var interrupted *BuildLogInterruptedError
			require.ErrorAs(t, err, &interrupted)
			require.Equal(t, "checkpoint/1+=", interrupted.Cursor)
			var cause *Error
			require.ErrorAs(t, errors.Unwrap(err), &cause)
			require.Equal(t, tt.code, cause.Code)
			if tt.code == CodeUnauthorized {
				require.ErrorIs(t, err, ErrAuthentication)
			}
		})
	}
}

func TestBuildLogInterruptedZeroValueIsSafe(t *testing.T) {
	// Given an interruption without a cause, such as a caller's test fixture.
	err := &BuildLogInterruptedError{}
	// When formatting and unwrapping it.
	var message string
	require.NotPanics(t, func() { message = err.Error() })
	cause := errors.Unwrap(err)
	// Then the zero value has a description, no typed-nil cause, and identity.
	require.NotEmpty(t, message)
	require.NoError(t, cause)
	require.ErrorIs(t, err, ErrBuildLogInterrupted)
	require.NotErrorIs(t, err, ErrBadRequest)
}
