// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"fmt"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSandboxInfoPortAssignmentBounds(t *testing.T) {
	for _, field := range []string{"host_port", "vm_port"} {
		for _, port := range []int{0, 1, 65535, 65536} {
			t.Run(fmt.Sprintf("%s/%d", field, port), func(t *testing.T) {
				// Given
				body := sandboxFixture("sbx")
				assignment := map[string]any{"host_port": 3000, "vm_port": 3000, "proto": "future-protocol"}
				assignment[field] = port
				body["port_map"] = []any{assignment}
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"GET /sandboxes/sbx": jsonHandler(t, body),
				})
				client := newTestClient(t, fake.Server)
				// When
				sandbox, err := client.Get(t.Context(), "sbx", nil)
				// Then
				if port == 0 || port == 65536 {
					require.ErrorIs(t, err, ErrBadRequest)
					require.Contains(t, err.Error(), field)
					return
				}
				require.NoError(t, err)
				got := sandbox.Info().PortMap[0]
				if field == "host_port" {
					require.Equal(t, int64(port), got.HostPort)
				} else {
					require.Equal(t, int64(port), got.VMPort)
				}
				require.Equal(t, "future-protocol", got.Protocol)
			})
		}
	}
}
