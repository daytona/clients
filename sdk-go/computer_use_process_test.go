// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

const desktopProcessFixture = `{
	"Running":true,"Priority":-1,"AutoRestart":true,"Pid":42,"Failed":false,
	"RestartAttempts":2,"RestartBackoffMs":1250,"Status":"running",
	"CPUUsage":0.25,"FutureField":7
}`

func TestComputerUseProcesses_whenInventoryRead(t *testing.T) {
	// Given
	sandbox, _ := computerUseTestSandbox(t, jsonHandler(t, map[string]JSONValue{
		"wm": JSONValue(desktopProcessFixture),
	}))
	// When
	got, err := sandbox.ComputerUse().Service().Status(t.Context())
	// Then
	require.NoError(t, err)
	require.Len(t, got, 1)
	status := got["wm"]
	require.True(t, status.Running)
	require.Equal(t, -1, status.Priority)
	require.True(t, status.AutoRestart)
	require.Equal(t, 42, *status.PID)
	require.False(t, status.Failed)
	require.Equal(t, 2, status.RestartAttempts)
	require.Equal(t, 1250*time.Millisecond, status.RestartBackoff)
	require.Equal(t, "running", status.Status)
	require.JSONEq(t, "0.25", string(status.ExtraFields["cpuUsage"]))
	require.JSONEq(t, "7", string(status.ExtraFields["futureField"]))
}

func TestComputerUseProcesses_whenNamedOperationSent(t *testing.T) {
	runDesktopWireCases(t, []desktopWireCase{
		{
			"status escaped", "GET", "/computeruse/process/wm%2Fhelper/status", "", `{"processName":"wm/helper","running":true}`,
			func(ctx context.Context, c *ComputerUse) error {
				got, err := c.Service().ProcessStatus(ctx, "wm/helper")
				if err == nil {
					require.Equal(t, "wm/helper", got.ProcessName)
					require.True(t, got.Running)
				}
				return err
			},
		},
		{
			"restart", "POST", "/computeruse/process/wm/restart", "", `{"processName":"wm","message":"restarted"}`,
			func(ctx context.Context, c *ComputerUse) error {
				got, err := c.Service().Restart(ctx, "wm")
				if err == nil {
					require.Equal(t, "restarted", got.Message)
				}
				return err
			},
		},
		{
			"logs", "GET", "/computeruse/process/wm/logs", "", `{"processName":"wm","logs":"stdout"}`,
			func(ctx context.Context, c *ComputerUse) error {
				got, err := c.Service().Logs(ctx, "wm")
				if err == nil {
					require.Equal(t, "stdout", got.Logs)
				}
				return err
			},
		},
		{
			"errors", "GET", "/computeruse/process/wm/errors", "", `{"processName":"wm","errors":"stderr"}`,
			func(ctx context.Context, c *ComputerUse) error {
				got, err := c.Service().Errors(ctx, "wm")
				if err == nil {
					require.Equal(t, "stderr", got.Errors)
				}
				return err
			},
		},
	})
}
