// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build e2e

package e2e_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	daytona "go.daytona.com/sdk/v2"
)

func TestImagesBuildLifecycle(t *testing.T) {
	// Given a reusable Python image with a tiny package.
	client := newBuildClient(t)
	ctx := testContext(t)
	name := uniqueName(t, "gosdk-e2e-images")
	cleanupImage(t, client, name)
	builder := daytona.NewImageBuilder(smokeImage()).PipInstall("six==1.17.0")

	// When building and starting a sandbox from the resulting image.
	image, err := client.Images().BuildAndWait(ctx, builder, &daytona.BuildImageWaitOptions{
		BuildImageOptions: daytona.BuildImageOptions{Name: name},
		Timeout:           4 * time.Minute,
	})
	require.NoError(t, err)
	require.Equal(t, daytona.ImageStatusReady, image.Status)
	completed := false
	for event, followErr := range client.Images().FollowBuildLogs(ctx, image.ID, &daytona.FollowBuildLogsOptions{
		Timeout: time.Minute,
	}) {
		require.NoError(t, followErr)
		switch event.(type) {
		case daytona.BuildLogDoneEvent:
			completed = true
		case daytona.BuildLogStateEvent, daytona.BuildLogChunkEvent, daytona.BuildLogGapEvent,
			daytona.BuildLogUnavailableEvent, daytona.BuildLogSourceChangedEvent, daytona.BuildLogErrorEvent:
		}
	}
	require.True(t, completed, "build log observation must reach explicit completion")
	createImageSandbox(t, ctx, image.ID)

	// Then direct reads and the eventual index expose the same reusable image.
	read, err := client.Images().Get(ctx, image.ID, nil)
	require.NoError(t, err)
	require.Equal(t, name, read.Name)
	eventually(ctx, t, 30*time.Second, func() (bool, error) {
		page, listErr := client.Images().List(ctx, &daytona.ListImagesOptions{Name: name})
		if listErr != nil {
			return false, listErr
		}
		for _, item := range page.Items {
			if item.ID == image.ID {
				return true, nil
			}
		}
		return false, nil
	})
}

func TestImagesBuildContext(t *testing.T) {
	// Given a directory context, including a file copied into the image.
	client := newBuildClient(t)
	ctx := testContext(t)
	name := uniqueName(t, "gosdk-e2e-context")
	cleanupImage(t, client, name)
	directory := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(directory, "Dockerfile"),
		[]byte("FROM "+smokeImage()+"\nCOPY message.txt /message.txt\n"), 0o600))
	require.NoError(t, os.WriteFile(filepath.Join(directory, "message.txt"), []byte("context upload\n"), 0o600))

	// When building from that context.
	image, err := client.Images().BuildFromContext(ctx, directory, &daytona.BuildFromContextOptions{
		BuildImageOptions: daytona.BuildImageOptions{Name: name},
	})
	require.NoError(t, err)
	ready, err := client.Images().Get(ctx, image.ID, &daytona.GetArtifactOptions{Wait: true, Timeout: 4 * time.Minute})

	// Then storage verification and the build produce a usable image.
	require.NoError(t, err)
	require.Equal(t, daytona.ImageStatusReady, ready.Status, ready.Error)
}

func TestImagesListSnapshots(t *testing.T) {
	// Given a credentialed client.
	client := newClient(t)
	ctx := testContext(t)

	// When listing snapshots.
	page, err := client.Snapshots().List(ctx, &daytona.ListSnapshotsOptions{Limit: 1})

	// Then the endpoint yields a typed page, even when no snapshots exist.
	require.NoError(t, skipIfServerLacks(t, err, "listing snapshots (read:snapshots)"))
	require.LessOrEqual(t, len(page.Items), 1)
}

func TestImagesNotFound(t *testing.T) {
	// Given a name that does not identify a test image.
	client := newClient(t)
	ctx := testContext(t)
	name := uniqueName(t, "gosdk-e2e-missing-image")

	// When reading that image.
	_, err := client.Images().Get(ctx, name, nil)

	// Then callers can branch on the missing-resource category.
	require.ErrorIs(t, err, daytona.ErrNotFound)
}

func cleanupImage(t *testing.T, client *daytona.Client, name string) {
	t.Helper()
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
		defer cancel()
		for {
			err := client.Images().Delete(ctx, name)
			if err == nil || errors.Is(err, daytona.ErrNotFound) {
				return
			}
			// Sandbox deletion is asynchronous; retry only this documented conflict.
			if !errors.Is(err, daytona.ErrConflict) {
				t.Errorf("delete test image: %v", err)
				return
			}
			select {
			case <-ctx.Done():
				t.Errorf("delete test image before cleanup deadline: %v", err)
				return
			case <-time.After(time.Second):
			}
		}
	})
}

func createImageSandbox(t *testing.T, ctx context.Context, imageID string) {
	t.Helper()
	name := uniqueName(t, "gosdk-e2e-image-sandbox")
	body, err := json.Marshal(struct {
		Image  string            `json:"image"`
		Name   string            `json:"name"`
		Labels map[string]string `json:"labels"`
		Wait   int               `json:"wait_seconds"`
	}{imageID, name, map[string]string{labelKey: labelValue, "name": name}, 60})
	require.NoError(t, err)
	var created struct {
		ID string `json:"id"`
	}
	// Lifecycle is implemented in its own worktree; drive its public HTTP surface here.
	require.NoError(t, rawAPI(ctx, http.MethodPost, "/sandboxes", body, &created))
	t.Cleanup(func() {
		// Cleanup must still run if the test's operation deadline was exhausted.
		cleanupCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), time.Minute)
		defer cancel()
		if deleteErr := rawAPI(cleanupCtx, http.MethodDelete, "/sandboxes/"+created.ID, nil, nil); deleteErr != nil {
			t.Errorf("delete image sandbox: %v", deleteErr)
		}
	})
	require.NotEmpty(t, created.ID)
}
