// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build e2e

package e2e_test

import (
	"syscall"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	daytona "go.daytona.com/sdk/v2"
)

type closedProcessOutput struct{}

func (closedProcessOutput) Write([]byte) (int, error) { return 0, syscall.EPIPE }

func TestProcessOutputFailureLive(t *testing.T) {
	// Given a running sandbox and a caller whose output pipe is closed.
	client := newToolboxClient(t)
	ctx := testContext(t)
	id := rawSandbox(t, uniqueName(t, "gosdk-output-failure"))
	sandbox, err := client.Get(ctx, id, nil)
	require.NoError(t, err)

	// When a long-running process tries to deliver its first output.
	result, execErr := sandbox.Exec(ctx, "printf ready; exec sleep 120", &daytona.ExecOptions{
		Stdout: closedProcessOutput{},
	})

	// Then the error identifies the closed pipe and the returned process stops.
	require.ErrorIs(t, execErr, syscall.EPIPE)
	require.NotNil(t, result)
	require.NotEmpty(t, result.ProcessID)
	handle, connectErr := sandbox.Process().Connect(result.ProcessID)
	require.NoError(t, connectErr)
	terminal, waitErr := handle.Wait(ctx, &daytona.WaitProcessOptions{Timeout: 10 * time.Second})
	require.NoError(t, waitErr)
	require.NotEqual(t, daytona.ProcessReasonTimedOut, terminal.Reason)
}

func TestInterpreterOutputFailureLive(t *testing.T) {
	// Given an interpreter cell whose output destination is a closed pipe.
	client := newToolboxClient(t)
	ctx := testContext(t)
	id := rawSandbox(t, uniqueName(t, "gosdk-cell-output-failure"))
	sandbox, err := client.Get(ctx, id, nil)
	require.NoError(t, err)

	// When an otherwise unbounded cell writes its first output.
	result, runErr := sandbox.RunCode(ctx, "print('ready', flush=True)\nwhile True:\n    pass", &daytona.RunCodeOptions{
		Stdout: closedProcessOutput{},
	})

	// Then the original pipe error and cell identity survive interruption.
	require.ErrorIs(t, runErr, syscall.EPIPE)
	require.NotNil(t, result)
	require.NotEmpty(t, result.Process.ProcessID)
	handle, connectErr := sandbox.Process().Connect(result.Process.ProcessID)
	require.NoError(t, connectErr)
	terminal, waitErr := handle.Wait(ctx, &daytona.WaitProcessOptions{Timeout: 10 * time.Second})
	require.NoError(t, waitErr)
	require.NotEqual(t, daytona.ProcessReasonTimedOut, terminal.Reason)
}
