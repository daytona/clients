// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build e2e

package e2e_test

import (
	"bytes"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	daytona "go.daytona.com/sdk/v2"
)

func TestProcessLive(t *testing.T) {
	// Given one uniquely named, cleanup-owned running sandbox.
	client := newClient(t)
	ctx := testContext(t)
	id := rawSandbox(t, uniqueName(t, "gosdk-process"))
	sandbox, err := client.Get(ctx, id, nil)
	require.NoError(t, err)

	t.Run("exec stdout stderr exit code", func(t *testing.T) {
		// When a shell command writes both streams and exits unsuccessfully.
		result, execErr := sandbox.Exec(ctx, "printf hello; printf warning >&2; exit 7", nil)
		// Then non-zero exit is not an SDK error.
		require.NoError(t, execErr)
		require.Equal(t, 7, *result.ExitCode)
		require.Equal(t, "hello", result.Stdout)
		require.Equal(t, "warning", result.Stderr)
		require.Contains(t, result.Output, "hello")
		require.Contains(t, result.Output, "warning")
	})
	t.Run("argv and streaming", func(t *testing.T) {
		// When literal shell syntax is passed as an argument.
		var stdout bytes.Buffer
		result, execErr := sandbox.ExecArgs(ctx, []string{"printf", "%s", "$(false)"}, &daytona.ExecOptions{Stdout: &stdout})
		// Then no interpolation occurs and the streaming writer sees the output.
		require.NoError(t, execErr)
		require.Equal(t, "$(false)", result.Stdout)
		require.Equal(t, result.Stdout, stdout.String())
	})
	t.Run("background logs kill", func(t *testing.T) {
		// Given a tagged background command.
		handle, startErr := sandbox.Process().Start(ctx, "printf ready; exec sleep 120", &daytona.ProcessStartOptions{
			Tag: "background", KeepLogs: daytona.ProcessKeepLogsUntilCleanup,
		})
		require.NoError(t, startErr)
		// When observing readiness through the real log stream and stopping it.
		var seen strings.Builder
		for frame, logErr := range handle.Logs(ctx, nil) {
			require.NoError(t, logErr)
			seen.WriteString(frame.Data)
			if strings.Contains(seen.String(), "ready") {
				break
			}
		}
		require.NoError(t, handle.Kill(ctx, nil))
		result, waitErr := handle.Wait(ctx, &daytona.WaitProcessOptions{Timeout: 10 * time.Second})
		// Then readiness was delivered before termination.
		require.NoError(t, waitErr)
		require.Contains(t, seen.String(), "ready")
		require.NotEqual(t, daytona.ProcessReasonTimedOut, result.Reason)
	})
	t.Run("stdin", func(t *testing.T) {
		// Given an exec with open stdin.
		handle, startErr := sandbox.Process().Start(ctx, "cat", &daytona.ProcessStartOptions{Stdin: daytona.ProcessStdinOpen})
		require.NoError(t, startErr)
		// When bytes and EOF arrive.
		_, writeErr := handle.Write(ctx, []byte("input round trip\n"))
		require.NoError(t, writeErr)
		require.NoError(t, handle.CloseStdin(ctx))
		_, waitErr := handle.Wait(ctx, nil)
		require.NoError(t, waitErr)
		// Then replay contains exactly what cat received.
		var output strings.Builder
		for frame, logErr := range handle.Logs(ctx, nil) {
			require.NoError(t, logErr)
			if frame.Channel == daytona.LogChannelStdout {
				output.WriteString(frame.Data)
			}
		}
		require.Equal(t, "input round trip\n", output.String())
	})
	t.Run("pty round trip", func(t *testing.T) {
		// Given a live shell attached through WebSocket.
		connection, createErr := sandbox.PTY().Create(ctx, "sh", nil)
		require.NoError(t, createErr)
		defer func() { require.NoError(t, connection.Close()) }()
		require.NoError(t, connection.Resize(ctx, daytona.TerminalSize{Cols: 100, Rows: 30}))
		// When issuing a command whose result does not occur literally in echoed input.
		_, writeErr := connection.Write([]byte("printf 'pty-%s\\n' 42\n"))
		require.NoError(t, writeErr)
		var output strings.Builder
		buffer := make([]byte, 1024)
		for !strings.Contains(output.String(), "pty-42") {
			n, readErr := connection.Read(buffer)
			require.NoError(t, readErr)
			output.Write(buffer[:n])
		}
		// Then the terminal ran the command and can be stopped independently of disconnect.
		require.Contains(t, output.String(), "pty-42")
		require.NoError(t, connection.Handle().Delete(ctx))
	})
	t.Run("session state", func(t *testing.T) {
		// Given a persistent shell with state established by an earlier command.
		session, createErr := sandbox.Process().CreateSession(ctx, nil)
		require.NoError(t, createErr)
		defer func() { require.NoError(t, session.Delete(ctx)) }()
		_, runErr := session.Run(ctx, "export SDK_PROCESS_VALUE=retained; cd /tmp", nil)
		require.NoError(t, runErr)
		// When a later command reads that state.
		result, runErr := session.Run(ctx, `printf '%s:%s' "$PWD" "$SDK_PROCESS_VALUE"`, nil)
		// Then cwd and environment persist.
		require.NoError(t, runErr)
		require.Equal(t, "/tmp:retained", result.Stdout)
	})
	t.Run("interpreter state", func(t *testing.T) {
		// Given the default Python context has state from an earlier call.
		_, runErr := sandbox.RunCode(ctx, "sdk_process_answer = 40", nil)
		require.NoError(t, runErr)
		// When calling the convenience method again without an explicit context.
		result, runErr := sandbox.RunCode(ctx, "print(sdk_process_answer + 2)", nil)
		// Then implicit runs share kernel state.
		require.NoError(t, runErr)
		require.Nil(t, result.Error)
		require.Equal(t, "42\n", result.Logs.Stdout)
	})
	t.Run("missing process error", func(t *testing.T) {
		// When fetching an ID that does not exist.
		_, getErr := sandbox.Process().Get(ctx, "missing-gosdk-process")
		// Then the real wire error maps to the shared sentinel.
		require.ErrorIs(t, getErr, daytona.ErrProcessNotFound)
	})
}
