// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestDockerWait_whenClosedWithInvalidTimeout_returnsClientClosed(t *testing.T) {
	// Given a closed client and an invalid wait budget.
	fake := newFakeServer(t, nil)
	sandbox := dockerVolumeSandbox(t, fake)
	require.NoError(t, sandbox.client.Close())
	// When attempting a new wait.
	result, err := sandbox.Docker().WaitUntilReady(t.Context(), &DockerWaitOptions{Timeout: NoWait})
	// Then closed-client precedence applies before local option validation.
	require.ErrorIs(t, err, ErrClientClosed)
	require.Nil(t, result)
	require.Empty(t, fake.requests())
}
