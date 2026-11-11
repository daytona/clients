// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build unix

package cmd

import (
	"context"
	"os/exec"
	"syscall"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

// An interrupted CLI exits with its own interruption code, never the -1 of
// the ssh client it killed; SIGTERM maps to 143 at the top.
func TestSSHExitError_interruptedCLIWinsOverTheKilledClient(t *testing.T) {
	ctx, cancel := context.WithCancelCause(context.Background())
	time.AfterFunc(50*time.Millisecond, func() { cancel(errSignalled{Signal: syscall.SIGTERM}) })
	err := sshExitError(ctx, runSSHCommand(ctx, "/bin/sleep", []string{"5"}))
	require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
	require.Equal(t, cerr.ExitTerminated, interruptedExitCode(ctx, cerr.ExitCode(err)))
}

func TestSSHExitError_signalDeathAndPassthrough(t *testing.T) {
	cmd := exec.Command("/bin/sh", "-c", "kill -KILL $$")
	err := sshExitError(context.Background(), cmd.Run())
	require.Equal(t, 137, cerr.ExitCode(err))

	err = sshExitError(context.Background(), exec.Command("/bin/sh", "-c", "exit 42").Run())
	require.Equal(t, 42, cerr.ExitCode(err))
	require.Equal(t, cerr.SourceSSH, cerr.DetailsOf(err).Source)
}
