// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestBuildLogsR3DeadlineTypeDiagnostic(t *testing.T) {
	for _, value := range []string{"42", "false", "{}", "[]"} {
		t.Run(value, func(t *testing.T) {
			// Given a state event with a non-string deadline.
			body := "event: state\ndata: " + `{"status":"building","deadline_at":` + value + "}\n\n"
			fake := buildLogsFake(t, buildLogsSSE(t, body))
			client := newTestClient(t, fake.Server)
			// When consuming the build-log stream.
			_, err := buildLogsCollect(client.Images().FollowBuildLogs(t.Context(), "image", nil))
			// Then the protocol failure states the field and its required type.
			var interrupted *BuildLogInterruptedError
			require.ErrorAs(t, err, &interrupted)
			require.Equal(t, "BUILD_LOG_PROTOCOL_ERROR", interrupted.SDKError.Code)
			require.ErrorContains(t, err, "deadline_at must be a string")
			require.Len(t, fake.requests(), 2)
		})
	}
}
