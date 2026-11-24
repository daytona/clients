// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build unix

package buildcontext

import (
	"syscall"
	"testing"

	"github.com/stretchr/testify/require"
)

func fixtureFIFO(t *testing.T, filename string) {
	t.Helper()
	require.NoError(t, syscall.Mkfifo(filename, 0o600))
}
