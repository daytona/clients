// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build e2e

package e2e_test

import (
	"errors"
	"testing"

	daytona "go.daytona.com/sdk/v2"

	"github.com/stretchr/testify/require"
)

func TestComputerUseDesktop(t *testing.T) {
	// Given
	client := newClient(t)
	ctx := testContext(t)
	id := rawSandbox(t, uniqueName(t, "gosdk-computeruse"))
	sandbox, err := client.Get(ctx, id, nil)
	require.NoError(t, err)
	desktop := sandbox.ComputerUse()
	// When
	_, startErr := desktop.Start(ctx, nil)
	// Then
	if errors.Is(startErr, daytona.ErrAddonUnavailable) ||
		errors.Is(startErr, daytona.ErrUnsupportedOperation) ||
		errors.Is(startErr, daytona.ErrNotSupported) {
		t.Skipf("default sandbox image does not support the desktop addon: %v", startErr)
	}
	require.NoError(t, startErr)

	t.Run("status", func(t *testing.T) {
		status, statusErr := desktop.Status(ctx)
		require.NoError(t, statusErr)
		require.Equal(t, daytona.ComputerUseDesktopReady, status.Desktop)
	})
	t.Run("mouse", func(t *testing.T) {
		point := daytona.ComputerUsePoint{X: 10, Y: 20}
		moved, moveErr := desktop.Mouse().Move(ctx, point)
		require.NoError(t, moveErr)
		require.Equal(t, point, *moved)
	})
	t.Run("keyboard", func(t *testing.T) {
		require.NoError(t, desktop.Keyboard().Hotkey(ctx, "ctrl+l"))
	})
	t.Run("screenshot", func(t *testing.T) {
		image, imageErr := desktop.Screen().Screenshot(ctx, nil)
		require.NoError(t, imageErr)
		require.NotEmpty(t, image.Data)
	})
	t.Run("display", func(t *testing.T) {
		displays, displayErr := desktop.Display().Info(ctx)
		require.NoError(t, displayErr)
		require.NotEmpty(t, displays)
	})
	t.Run("processes", func(t *testing.T) {
		processes, processErr := desktop.Service().Status(ctx)
		require.NoError(t, processErr)
		require.NotEmpty(t, processes)
	})
	t.Run("accessibility", func(t *testing.T) {
		tree, treeErr := desktop.A11y().Tree(ctx, daytona.ComputerUseA11yScopeAll, nil)
		require.NoError(t, treeErr)
		require.NotEmpty(t, tree.Root.ID)
	})
	t.Run("recording", func(t *testing.T) {
		recording, recordErr := desktop.Recording().Start(ctx, nil)
		require.NoError(t, recordErr)
		t.Cleanup(func() { require.NoError(t, desktop.Recording().Delete(ctx, recording.ID)) })
		_, stopErr := desktop.Recording().Stop(ctx, recording.ID)
		require.NoError(t, stopErr)
		data, downloadErr := desktop.Recording().Download(ctx, recording.ID, nil)
		require.NoError(t, downloadErr)
		require.NotEmpty(t, data)
	})
	t.Run("restart", func(t *testing.T) {
		restarted, restartErr := desktop.Restart(ctx, nil)
		require.NoError(t, restartErr)
		require.Equal(t, daytona.ComputerUseDesktopReady, restarted.Desktop)
	})
	t.Run("stop", func(t *testing.T) {
		_, stopErr := desktop.Stop(ctx)
		require.NoError(t, stopErr)
	})
}
