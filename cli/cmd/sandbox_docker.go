// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"strconv"
	"time"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	toolboxclient "github.com/daytona/clients/cli/internal/toolbox"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

const (
	dockerStatusMaxWait      = time.Second
	dockerStatusPollInterval = 500 * time.Millisecond
)

func newSandboxDockerSubcommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "docker",
		Short: "Inspect Docker Engine inside a sandbox",
		Long:  "Inspect Docker Engine inside a sandbox.\nExamples:\n  daytona sandbox docker status <id>\n  daytona sandbox docker status <id> --wait",
	}
	cmd.AddCommand(newSandboxDockerStatusCommand())
	return cmd
}

func newSandboxDockerStatusCommand() *cobra.Command {
	waitForReady := false
	cmd := &cobra.Command{
		Use:   "status <sandbox-id>",
		Short: "Read Docker Engine status inside a sandbox",
		Long:  "Read Docker Engine status inside a sandbox. --wait polls until Docker is ready; if --timeout is not set, the command uses the SDK default wait budget of 60s.",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			var status api.Raw[tbgen.DockerStatus]
			if waitForReady {
				timeout, err := workload.ParseTimeout(a.Globals.Timeout)
				if err != nil {
					return err
				}
				runCtx, cancel := context.WithTimeout(cmd.Context(), timeout)
				defer cancel()
				toolClient, _, _, err := sandboxToolboxClient(runCtx, a, args[0])
				if err != nil {
					if errors.Is(runCtx.Err(), context.DeadlineExceeded) {
						return sandboxDockerTimeoutError(timeout, nil)
					}
					return err
				}
				status, err = waitForSandboxDockerReady(runCtx, toolClient, args[0], timeout)
				if err != nil {
					return err
				}
			} else {
				toolClient, _, _, err := sandboxToolboxClient(cmd.Context(), a, args[0])
				if err != nil {
					return err
				}
				status, err = sandboxDockerStatus(cmd.Context(), toolClient, 0)
				if err != nil {
					return err
				}
			}
			return printToolboxObject(a, status, sandboxDockerStatusKV(status.V))
		},
	}
	cmd.Flags().BoolVar(&waitForReady, "wait", false, "wait until Docker reaches ready")
	addTimeoutFlag(cmd, "60s", "with --wait, how long to wait for Docker (Go duration)")
	return cmd
}

func sandboxDockerStatus(ctx context.Context, toolClient *toolboxclient.Client, waitFor time.Duration) (api.Raw[tbgen.DockerStatus], error) {
	query := url.Values{}
	if waitFor > 0 {
		waitMs := waitFor.Milliseconds()
		if waitMs <= 0 {
			waitMs = 1
		}
		if waitMs > dockerStatusMaxWait.Milliseconds() {
			waitMs = dockerStatusMaxWait.Milliseconds()
		}
		query.Set("waitMs", strconv.FormatInt(waitMs, 10))
	}
	var status api.Raw[tbgen.DockerStatus]
	if err := toolClient.GetJSON(ctx, "/docker/status", query, &status); err != nil {
		return api.Raw[tbgen.DockerStatus]{}, err
	}
	return status, nil
}

func waitForSandboxDockerReady(ctx context.Context, toolClient *toolboxclient.Client, sandboxID string, timeout time.Duration) (api.Raw[tbgen.DockerStatus], error) {
	deadline := time.Now().Add(timeout)
	var last *tbgen.DockerStatus
	for {
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return api.Raw[tbgen.DockerStatus]{}, sandboxDockerTimeoutError(timeout, last)
		}
		pollStarted := time.Now()
		statusCtx, cancel := context.WithTimeout(ctx, remaining)
		status, err := sandboxDockerStatus(statusCtx, toolClient, remaining)
		localTimeout := errors.Is(statusCtx.Err(), context.DeadlineExceeded) && ctx.Err() == nil
		cancel()
		if localTimeout || errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return api.Raw[tbgen.DockerStatus]{}, sandboxDockerTimeoutError(timeout, last)
		}
		if err != nil {
			return api.Raw[tbgen.DockerStatus]{}, err
		}
		last = &status.V
		if time.Until(deadline) <= 0 {
			return api.Raw[tbgen.DockerStatus]{}, sandboxDockerTimeoutError(timeout, last)
		}
		if status.V.State == tbgen.DockerStateReady {
			return status, nil
		}
		if sandboxDockerTerminal(status.V) {
			return api.Raw[tbgen.DockerStatus]{}, sandboxDockerNotReadyError(sandboxID, status.V)
		}
		sleep := dockerStatusPollInterval - time.Since(pollStarted)
		if sleep <= 0 {
			continue
		}
		if sleep > time.Until(deadline) {
			sleep = time.Until(deadline)
		}
		timer := time.NewTimer(sleep)
		select {
		case <-ctx.Done():
			timer.Stop()
			return api.Raw[tbgen.DockerStatus]{}, cerr.New(cerr.ExitInterrupted, "interrupted")
		case <-timer.C:
		}
	}
}

func sandboxDockerTerminal(status tbgen.DockerStatus) bool {
	switch status.State {
	case tbgen.DockerStateFailed, tbgen.DockerStateNotPresent, tbgen.DockerStateDisabled, tbgen.DockerStateUnmanaged, tbgen.DockerStateUnsupported:
		return true
	default:
		return false
	}
}

func sandboxDockerNotReadyError(sandboxID string, status tbgen.DockerStatus) error {
	message := fmt.Sprintf("docker is %s", status.State)
	if reason := derefString(status.Reason); reason != "" {
		message += ": " + reason
	}
	e := cerr.New(cerr.ExitConflict, "%s", message)
	if processID := derefString(status.ProcessId); processID != "" {
		e = e.WithHint("inspect Docker logs with `daytona sandbox process logs %s %s`", sandboxID, processID)
	}
	return e
}

func sandboxDockerTimeoutError(timeout time.Duration, status *tbgen.DockerStatus) error {
	message := fmt.Sprintf("timed out after %s waiting for Docker to become ready", timeout)
	if status == nil {
		return cerr.New(cerr.ExitTimeout, "%s", message)
	}
	message += fmt.Sprintf(" (last state %s", status.State)
	if reason := derefString(status.Reason); reason != "" {
		message += ": " + reason
	}
	message += ")"
	return cerr.New(cerr.ExitTimeout, "%s", message)
}

func sandboxDockerStatusKV(status tbgen.DockerStatus) [][2]string {
	rows := [][2]string{{"state", string(status.State)}, {"owner", string(status.Owner)}}
	if reason := derefString(status.Reason); reason != "" {
		rows = append(rows, [2]string{"reason", reason})
	}
	if processID := derefString(status.ProcessId); processID != "" {
		rows = append(rows, [2]string{"process_id", processID})
	}
	return rows
}
