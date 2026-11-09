// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package workload

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/wait"
)

type SandboxWaitOptions struct {
	ID                  string
	Target              string
	Timeout             time.Duration
	AllowInitialStopped bool
	// A macOS pause the Mac cannot finish puts the sandbox back to running. The pause POST commits
	// pausing before it answers, and the API holds `?wait=paused` through pausing, so any running
	// answer after the POST is that failed pause: a conflict, as in the SDKs' macOS pause.
	RunningAgainFails bool
	// A macOS fork can finish running or stopped. With Target running, a stopped
	// response also completes the wait; a stopping response does not.
	StoppedSettles bool
}

func ParseTimeout(raw string) (time.Duration, error) {
	timeout, err := time.ParseDuration(raw)
	if err != nil {
		return 0, cerr.Usage("invalid --timeout %q: %v", raw, err)
	}
	if timeout <= 0 {
		return 0, cerr.Usage("--timeout must be greater than zero")
	}
	return timeout, nil
}

func WaitForSandbox(ctx context.Context, client *api.Client, options SandboxWaitOptions) (api.Raw[gen.Sandbox], error) {
	queryKey := "wait"
	if options.Target == "stopped" || options.Target == "deleted" {
		queryKey = "wait_exact"
	}
	// A wait-for-running that OBSERVES the sandbox drop into stopping/stopped
	// has lost its race and should say so — but the first observation may
	// legitimately be the state we are leaving: `start` on a stopped sandbox
	// long-polls `?wait=running`, and the server answers at once with the
	// still-`stopped` status before the transition becomes visible. So a settled
	// state counts as a conflict only after the sandbox was seen somewhere
	// else first.
	var seenProgress bool
	return wait.Until(ctx, options.Timeout, 200*time.Millisecond,
		func(ctx context.Context, remaining time.Duration) (api.Raw[gen.Sandbox], error) {
			var detail api.Raw[gen.Sandbox]
			query := url.Values{
				queryKey:     {options.Target},
				"timeout_ms": {strconv.FormatInt(longPollMillis(remaining), 10)},
			}
			err := client.Do(ctx, http.MethodGet, fmt.Sprintf("/sandboxes/%s", url.PathEscape(options.ID)), query, nil, &detail)
			return detail, err
		},
		func(raw api.Raw[gen.Sandbox]) (bool, error) {
			detail := raw.V
			if detail.Status == options.Target {
				return true, nil
			}
			if detail.Status == "running" && options.RunningAgainFails {
				return false, cerr.New(cerr.ExitConflict, "sandbox %s reached %s, not %s", options.ID, detail.Status, options.Target)
			}
			switch detail.Status {
			case "failed", "lost":
				if detail.Error != nil && *detail.Error != "" {
					return false, cerr.New(cerr.ExitConflict, "sandbox %s reached %s, not %s: %s", options.ID, detail.Status, options.Target, *detail.Error)
				}
				return false, cerr.New(cerr.ExitConflict, "sandbox %s reached %s, not %s", options.ID, detail.Status, options.Target)
			case "deleted":
				return false, cerr.New(cerr.ExitConflict, "sandbox %s reached %s, not %s", options.ID, detail.Status, options.Target)
			case "deleting":
				if options.Target == "deleted" {
					return false, nil
				}
				return false, cerr.New(cerr.ExitConflict, "sandbox %s is being deleted, will not reach %s", options.ID, options.Target)
			case "stopping", "stopped":
				if options.Target != "running" {
					return false, nil
				}
				if options.StoppedSettles {
					return detail.Status == "stopped", nil
				}
				if detail.Status == "stopped" && options.AllowInitialStopped && !seenProgress {
					return false, nil
				}
				return false, cerr.New(cerr.ExitConflict, "sandbox %s reached %s, not %s", options.ID, detail.Status, options.Target)
			default:
				seenProgress = true
				return false, nil
			}
		},
		nil,
	)
}

func WaitForImage(ctx context.Context, client *api.Client, id string, timeout time.Duration) (api.Raw[gen.Image], error) {
	return wait.Until(ctx, timeout, 200*time.Millisecond,
		func(ctx context.Context, remaining time.Duration) (api.Raw[gen.Image], error) {
			var image api.Raw[gen.Image]
			query := url.Values{
				"wait":       {"ready"},
				"timeout_ms": {strconv.FormatInt(longPollMillis(remaining), 10)},
			}
			err := client.Do(ctx, http.MethodGet, fmt.Sprintf("/images/%s", url.PathEscape(id)), query, nil, &image)
			return image, err
		},
		func(image api.Raw[gen.Image]) (bool, error) {
			if image.V.Status == "ready" {
				return true, nil
			}
			if image.V.Status == "failed" {
				return false, cerr.New(cerr.ExitFailure, "image %s reached failed, not ready", id)
			}
			return false, nil
		},
		nil,
	)
}

func WaitForSnapshot(ctx context.Context, client *api.Client, id string, timeout time.Duration) (api.Raw[gen.Snapshot], error) {
	return wait.Until(ctx, timeout, 200*time.Millisecond,
		func(ctx context.Context, remaining time.Duration) (api.Raw[gen.Snapshot], error) {
			var snapshot api.Raw[gen.Snapshot]
			query := url.Values{
				"wait":       {"ready"},
				"timeout_ms": {strconv.FormatInt(longPollMillis(remaining), 10)},
			}
			err := client.Do(ctx, http.MethodGet, fmt.Sprintf("/snapshots/%s", url.PathEscape(id)), query, nil, &snapshot)
			return snapshot, err
		},
		func(snapshot api.Raw[gen.Snapshot]) (bool, error) {
			if snapshot.V.Status == "ready" {
				return true, nil
			}
			if snapshot.V.Status == "failed" {
				return false, cerr.New(cerr.ExitFailure, "snapshot %s reached failed, not ready", id)
			}
			return false, nil
		},
		nil,
	)
}

func longPollMillis(timeout time.Duration) int64 {
	chunk := timeout
	if chunk > 55*time.Second {
		chunk = 55 * time.Second
	}
	if chunk < 0 {
		chunk = 0
	}
	if chunk == 0 {
		return 0
	}
	return int64((chunk + time.Millisecond - 1) / time.Millisecond)
}
