// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"fmt"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestDockerR3Status_whenOptionalStringsAreAbsentNullOrEmpty(t *testing.T) {
	for _, optional := range []string{"", `,"processId":null,"reason":null`, `,"processId":"","reason":""`} {
		t.Run(optional, func(t *testing.T) {
			// Given
			body := fmt.Sprintf(`{"owner":"daemon","state":"ready"%s}`, optional)
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /docker/status": jsonHandler(t, JSONValue(body))})
			sandbox := dockerVolumeSandbox(t, fake)
			// When
			status, err := sandbox.Docker().Status(t.Context())
			// Then
			require.NoError(t, err)
			if optional == `,"processId":"","reason":""` {
				require.Equal(t, new(""), status.ProcessID)
				require.Equal(t, new(""), status.Reason)
			} else {
				require.Nil(t, status.ProcessID)
				require.Nil(t, status.Reason)
			}
		})
	}
}
