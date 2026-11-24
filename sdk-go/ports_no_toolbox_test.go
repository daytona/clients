// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"testing"

	"github.com/stretchr/testify/require"
)

// The TypeScript SDK reports UNSUPPORTED_OPERATION here (unit.test.ts, "ports.list()
// and ports.isInUse() on a handle without a toolbox URL").
func TestPorts_whenHandleHasNoToolboxURL_reportsUnsupportedOperation(t *testing.T) {
	// Given a handle the API returned without a toolbox URL
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	sandbox, newErr := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "sbx"}})
	require.NoError(t, newErr)
	for name, call := range map[string]func() error{
		"List":    func() error { _, listErr := sandbox.Ports().List(t.Context()); return listErr },
		"IsInUse": func() error { _, useErr := sandbox.Ports().IsInUse(t.Context(), 3000); return useErr },
	} {
		t.Run(name, func(t *testing.T) {
			// When
			err := call()
			// Then
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, CodeUnsupportedOperation, detail.Code)
			require.Equal(t, 400, detail.StatusCode)
			require.Equal(t, SourceSDK, detail.Source)
			require.Contains(t, detail.Message, "sandbox handle has no toolbox URL; fetch the sandbox with Get")
			require.Empty(t, fake.requests())
		})
	}
}
