// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build e2e

package e2e_test

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	daytona "go.daytona.com/sdk/v2"
)

func TestCreateFromBuild(t *testing.T) {
	// Given a tiny Python image build and a labelled sandbox.
	client := newClient(t)
	ctx := testContext(t)
	builder := daytona.NewImageBuilder("python:3.12-slim").PipInstall("six==1.17.0")

	// When building and booting with separate readiness and create budgets.
	sandbox, err := client.CreateFromBuild(ctx, builder, &daytona.CreateFromBuildOptions{
		CreateOptions: daytona.CreateOptions{
			Name: uniqueName(t, "gosdk-e2e-build-create"), Labels: map[string]string{labelKey: labelValue},
			Wait: time.Minute,
		},
		BuildTimeout: 4 * time.Minute, IdleExpiry: time.Hour,
	})
	if sandbox != nil {
		// Cleanup registration is LIFO: delete the sandbox before its image.
		if image := sandbox.Info().Image; image != nil {
			cleanupImage(t, client, image.ID)
		}
		t.Cleanup(func() {
			cleanupCtx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
			defer cancel()
			require.NoError(t, sandbox.Delete(cleanupCtx, nil))
		})
	} else {
		var failure *daytona.ImageBuildFailedError
		if errors.As(err, &failure) {
			cleanupImage(t, client, failure.ImageID)
		}
	}
	require.NoError(t, err)
	require.NotNil(t, sandbox.Info().Image)
	require.Equal(t, daytona.SandboxStateStarted, sandbox.State())

	// Then the sandbox runs the package installed by the build.
	result, execErr := sandbox.Exec(ctx, `python3 -c "import six; print(six.__version__)"`, nil)
	require.NoError(t, execErr)
	require.Equal(t, "1.17.0\n", result.Stdout)
	require.NotNil(t, result.ExitCode)
	require.Zero(t, *result.ExitCode)
}
