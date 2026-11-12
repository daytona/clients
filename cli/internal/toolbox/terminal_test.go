// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"testing"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/stretchr/testify/require"
)

func TestTerminalSize_usesDefault_whenDescriptorCannotBeRead(t *testing.T) {
	// Given / When
	size := terminalSize(-1)
	// Then
	require.Equal(t, tbgen.TerminalSize{Cols: 80, Rows: 24}, size)
}
