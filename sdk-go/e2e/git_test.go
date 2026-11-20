// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build e2e

package e2e_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	daytona "go.daytona.com/sdk/v2"
)

func TestGitCloneCommitHistory(t *testing.T) {
	// Given: a fresh sandbox and the public sample repository.
	git, ctx, _ := gitLiveRepository(t)
	// When: commit a real file on the new branch.
	commit, err := git.Commit(ctx, gitLivePath, "Add SDK test file", &daytona.GitCommitOptions{
		Author: "SDK Test", Email: "sdk-test@example.org",
	})
	// Then: history identifies that exact commit, not a later HEAD lookup.
	require.NoError(t, err)
	history, err := git.History(ctx, gitLivePath)
	require.NoError(t, err)
	require.NotEmpty(t, history.Items)
	require.Equal(t, commit.Hash, history.Items[0].Hash)
	require.Equal(t, "Add SDK test file", history.Items[0].Message)
	require.Equal(t, "SDK Test", history.Items[0].Author)
}

func TestGitSafeBranchDeletion(t *testing.T) {
	// Given: an unmerged commit on a branch that is not checked out.
	git, ctx, originalBranch := gitLiveRepository(t)
	_, err := git.Commit(ctx, gitLivePath, "Unmerged SDK test file", &daytona.GitCommitOptions{
		Author: "SDK Test", Email: "sdk-test@example.org",
	})
	require.NoError(t, err)
	require.NoError(t, git.CheckoutBranch(ctx, gitLivePath, originalBranch))
	// When: the default must request safe deletion, not git branch -D.
	err = git.DeleteBranch(ctx, gitLivePath, "sdk-go-feature", nil)
	// Then: retain this contract check even on deployments predating safe deletion.
	require.ErrorIs(t, err, daytona.ErrConflict)
}

const gitLivePath = "/tmp/gosdk-git-repo"

func gitLiveRepository(t *testing.T) (*daytona.Git, context.Context, string) {
	t.Helper()
	client := newClient(t)
	ctx := testContext(t)
	id := rawSandbox(t, uniqueName(t, "gosdk-git"))
	sandbox, err := client.Get(ctx, id, nil)
	require.NoError(t, err)
	git := sandbox.Git()
	require.NoError(t, git.Clone(ctx, "https://github.com/octocat/Hello-World", gitLivePath, nil))
	original, err := git.Status(ctx, gitLivePath)
	require.NoError(t, err)
	require.NoError(t, git.CreateBranch(ctx, gitLivePath, "sdk-go-feature"))
	require.NoError(t, git.CheckoutBranch(ctx, gitLivePath, "sdk-go-feature"))
	portsExecRaw(t, ctx, id, "printf 'sdk git e2e\\n' > /tmp/gosdk-git-repo/sdk-go.txt")
	require.NoError(t, git.Add(ctx, gitLivePath, []string{"sdk-go.txt"}))
	return git, ctx, original.CurrentBranch
}

func TestDockerReadinessLive(t *testing.T) {
	// Given
	client := newClient(t)
	ctx := testContext(t)
	id := rawSandbox(t, uniqueName(t, "gosdk-git-docker"))
	sandbox, err := client.Get(ctx, id, nil)
	require.NoError(t, err)
	status, err := sandbox.Docker().Status(ctx)
	require.NoError(t, err)
	require.NotEmpty(t, status.RawState)
	// When
	ready, err := sandbox.Docker().WaitUntilReady(ctx, &daytona.DockerWaitOptions{Timeout: time.Second})
	// Then: the default image may omit Docker; that is a structured terminal outcome.
	var notReady *daytona.DockerNotReadyError
	var timeout *daytona.DockerWaitTimeoutError
	switch {
	case err == nil:
		require.Equal(t, daytona.DockerStateReady, ready.State)
	case errors.As(err, &notReady):
		require.ErrorIs(t, err, daytona.ErrConflict)
		require.NotEmpty(t, notReady.Status.RawState)
	case errors.As(err, &timeout):
		require.ErrorIs(t, err, daytona.ErrRequestTimeout)
	default:
		require.NoError(t, err)
	}
}

func TestSandboxVolumePullLive(t *testing.T) {
	// Given: a bucket volume attached to a fresh sandbox.
	client := newClient(t)
	ctx := testContext(t)
	name := uniqueName(t, "gosdk-git-volume")
	volumeBody, err := json.Marshal(map[string]string{"name": name, "type": "bucket"})
	require.NoError(t, err)
	var volume struct {
		ID string `json:"id"`
	}
	require.NoError(t, rawAPI(ctx, http.MethodPost, "/volumes", volumeBody, &volume))
	require.NotEmpty(t, volume.ID)
	t.Cleanup(func() {
		cleanupCtx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
		defer cancel()
		eventually(cleanupCtx, t, 2*time.Minute, func() (bool, error) {
			// Sandbox teardown releases the attachment asynchronously.
			return rawAPI(cleanupCtx, http.MethodDelete, "/volumes/"+volume.ID, nil, nil) == nil, nil
		})
	})
	body, err := json.Marshal(map[string]any{
		"name": name, "labels": map[string]string{labelKey: labelValue, "name": name}, "wait_seconds": 60,
		"volumes": []map[string]string{{"volume": volume.ID, "mount_path": "/mnt/gosdk-volume"}},
	})
	require.NoError(t, err)
	var created struct {
		ID string `json:"id"`
	}
	require.NoError(t, rawAPI(ctx, http.MethodPost, "/sandboxes", body, &created))
	require.NotEmpty(t, created.ID)
	t.Cleanup(func() {
		cleanupCtx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
		defer cancel()
		require.NoError(t, rawAPI(cleanupCtx, http.MethodDelete, "/sandboxes/"+created.ID, nil, nil))
	})
	eventually(ctx, t, time.Minute, func() (bool, error) {
		var observed struct {
			Status string `json:"status"`
		}
		readErr := rawAPI(ctx, http.MethodGet, "/sandboxes/"+created.ID, nil, &observed)
		return observed.Status == "running", readErr
	})
	sandbox, err := client.Get(ctx, created.ID, nil)
	require.NoError(t, err)
	requestID := uniqueName(t, "sdk-pull")
	// When: one submit/check must retain the caller's identity.
	result, err := sandbox.Volumes().Pull(ctx, volume.ID, requestID, &daytona.PullVolumeOptions{Timeout: daytona.NoWait})
	// Then
	require.NoError(t, err)
	require.Equal(t, requestID, result.RequestID)
	require.Contains(t, []daytona.PullVolumeStatus{
		daytona.PullVolumeStatusPulled, daytona.PullVolumeStatusUnchanged, daytona.PullVolumeStatusPending,
	}, result.Status)
}
