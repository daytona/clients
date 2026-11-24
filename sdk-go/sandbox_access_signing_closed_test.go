// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSandboxSigningRotationClosedClientPrecedesScopeValidation(t *testing.T) {
	for _, direct := range []string{"/toolbox", "/toolbox/sbx"} {
		t.Run(direct, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			sandbox, err := client.newSandbox(sandboxInit{
				Info:    SandboxInfo{ID: "sbx"},
				Options: GetSandboxOptions{ToolboxURL: fake.Server.URL + direct},
			})
			require.NoError(t, err)
			require.NoError(t, client.Close())
			// When
			err = sandbox.RotateSigningKey(t.Context())
			// Then
			require.ErrorIs(t, err, ErrClientClosed)
			require.Empty(t, fake.requests())
		})
	}
}
