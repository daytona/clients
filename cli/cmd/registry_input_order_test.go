// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"os"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestRegistrySet_preservesStdin_whenNewUsernameMissing(t *testing.T) {
	// Given a missing registry and unread password input.
	api := newFakeAPI(t)
	input, err := os.CreateTemp(t.TempDir(), "password")
	require.NoError(t, err)
	defer input.Close()
	_, err = input.WriteString("unused-password\n")
	require.NoError(t, err)
	_, err = input.Seek(0, 0)
	require.NoError(t, err)
	previous := os.Stdin
	os.Stdin = input
	t.Cleanup(func() { os.Stdin = previous })
	// When creating without the mandatory username.
	_, stderr, code := runMgmt(t, api, cfgDir(t), "registry", "set", "registry.example.com", "--password-stdin")
	// Then validation fails without consuming the credential.
	require.Equal(t, cerr.ExitUsage, code, stderr)
	position, err := input.Seek(0, 1)
	require.NoError(t, err)
	require.Zero(t, position)
}
