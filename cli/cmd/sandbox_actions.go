// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"fmt"
	"net/http"
	"net/url"
	"os"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/input"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

func newSandboxStartCommand() *cobra.Command {
	return newSandboxActionCommand("start", func(cmd *cobra.Command, resolved workload.ResolvedSandbox) (any, string, string, error) {
		return sandboxJobAction[gen.AsyncOperation](cmd, resolved, "start")
	})
}

func newSandboxStopCommand() *cobra.Command {
	return newSandboxActionCommand("stop", func(cmd *cobra.Command, resolved workload.ResolvedSandbox) (any, string, string, error) {
		return sandboxJobAction[gen.AsyncOperation](cmd, resolved, "stop")
	})
}

func newSandboxPauseCommand() *cobra.Command {
	return newSandboxActionCommand("pause", func(cmd *cobra.Command, resolved workload.ResolvedSandbox) (any, string, string, error) {
		return sandboxJobAction[gen.AsyncOperation](cmd, resolved, "pause")
	})
}

func newSandboxResumeCommand() *cobra.Command {
	return newSandboxActionCommand("resume", func(cmd *cobra.Command, resolved workload.ResolvedSandbox) (any, string, string, error) {
		return sandboxJobAction[gen.AsyncOperation](cmd, resolved, "resume")
	})
}

func newSandboxForkCommand() *cobra.Command {
	name := ""
	cmd := newSandboxActionCommand("fork", func(cmd *cobra.Command, resolved workload.ResolvedSandbox) (any, string, string, error) {
		macos := resolved.Detail.Class == workload.ClassMacOS
		if resolved.Detail.Class != workload.ClassLinux && !macos {
			return nil, "", "", cerr.New(cerr.ExitConflict, "cannot fork sandbox %s with unknown class %s", resolved.Detail.Id, resolved.Detail.Class)
		}
		body := (*gen.ForkSandboxRequest)(nil)
		if cmd.Flags().Changed("name") {
			body = &gen.ForkSandboxRequest{Name: &name}
		}
		var resp gen.ForkSandboxOperation
		if err := resolved.Client.Do(cmd.Context(), http.MethodPost, fmt.Sprintf("/sandboxes/%s/fork", url.PathEscape(resolved.Detail.Id)), nil, body, &resp); err != nil {
			return nil, "", "", err
		}
		// Only a fork of a running source comes up by itself; a Linux
		// memory fork of another rests paused, a macOS disk fork stopped.
		waitFor := "paused"
		switch {
		case resolved.Detail.Status == "running":
			waitFor = "running"
		case macos:
			waitFor = "stopped"
		}
		return resp, resp.Id, waitFor, nil
	})
	cmd.Flags().StringVar(&name, "name", "", "fork name")
	return cmd
}

func newSandboxDeleteCommand() *cobra.Command {
	return newSandboxActionCommand("delete", func(cmd *cobra.Command, resolved workload.ResolvedSandbox) (any, string, string, error) {
		a := FromContext(cmd.Context())
		target := resolved.Detail.Name
		if target == "" {
			target = resolved.Detail.Id
		}
		if err := a.Confirm("delete", target); err != nil {
			return nil, "", "", err
		}
		return sandboxJobAction[gen.AsyncOperation](cmd, resolved, "delete")
	})
}

type sandboxUpdateFlags struct {
	labels          []string
	clearLabels     bool
	timeoutSecs     int64
	onTimeout       string
	idleTimeoutSecs int64
	onIdleTimeout   string
}

func newSandboxUpdateCommand() *cobra.Command {
	flags := sandboxUpdateFlags{}
	cmd := &cobra.Command{
		Use:   "update <id>",
		Short: "Update sandbox labels or lifecycle",
		Long:  "Update sandbox labels or lifecycle.\nExamples:\n  daytona sandbox update <id> --label env=dev\n  daytona sandbox update <id> --idle-timeout 600 --on-idle-timeout stop",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			home, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			resolved, err := workload.ResolveSandbox(cmd.Context(), home, workload.NewCatalogCache(), args[0])
			if err != nil {
				return err
			}
			fileBody, err := input.Load(a.Globals.File, os.Stdin, false)
			if err != nil {
				return err
			}
			labelUpdate := hasLabelUpdate(cmd, fileBody, flags)
			lifecycleUpdate := hasLifecycleUpdate(cmd, fileBody)
			if !labelUpdate && !lifecycleUpdate {
				return cerr.Usage("nothing to update").WithHint("pass --label, --clear-labels, lifecycle flags, or --file")
			}
			var labelsBody gen.SetSandboxLabelsRequest
			if labelUpdate {
				labelsBody, err = buildSandboxLabelsBody(cmd, fileBody, flags)
				if err != nil {
					return err
				}
			}
			var lifecycleBody gen.UpdateSandboxLifecyclePolicyRequest
			if lifecycleUpdate {
				lifecycleBody, err = buildSandboxLifecycleBody(cmd, fileBody, flags)
				if err != nil {
					return err
				}
				if err := validateSandboxLifecycleUpdate(string(resolved.Detail.Class), lifecycleBody); err != nil {
					return err
				}
			}
			if labelUpdate {
				var resp gen.SandboxLabels
				if err := resolved.Client.Do(cmd.Context(), http.MethodPut, fmt.Sprintf("/sandboxes/%s/labels", url.PathEscape(resolved.Detail.Id)), nil, labelsBody, &resp); err != nil {
					return err
				}
			}
			if lifecycleUpdate {
				var resp gen.SandboxLifecyclePolicy
				if err := resolved.Client.Do(cmd.Context(), http.MethodPatch, fmt.Sprintf("/sandboxes/%s/lifecycle-policy", url.PathEscape(resolved.Detail.Id)), nil, lifecycleBody, &resp); err != nil {
					return err
				}
			}
			return printSandboxDetail(cmd.Context(), args[0])
		},
	}
	cmd.Flags().StringArrayVar(&flags.labels, "label", nil, "replacement label KEY=VALUE (repeatable)")
	cmd.Flags().BoolVar(&flags.clearLabels, "clear-labels", false, "replace the label set with {}")
	cmd.Flags().Int64Var(&flags.timeoutSecs, "timeout", 0, "wall-clock seconds until --on-timeout fires (re-anchors the deadline from now)")
	cmd.Flags().StringVar(&flags.onTimeout, "on-timeout", "", "action when --timeout elapses: stop|pause|delete (stop and pause need a persistent macOS sandbox)")
	cmd.Flags().Int64Var(&flags.idleTimeoutSecs, "idle-timeout", 0, "idle seconds until --on-idle-timeout fires (-1 clears the pair)")
	cmd.Flags().StringVar(&flags.onIdleTimeout, "on-idle-timeout", "", "action when --idle-timeout elapses: stop|pause|delete (stop and pause need a persistent macOS sandbox)")
	return cmd
}

func newSandboxActionCommand(verb string, run func(cmd *cobra.Command, resolved workload.ResolvedSandbox) (any, string, string, error)) *cobra.Command {
	return &cobra.Command{
		Use:   verb + " <id>",
		Short: strings.ToUpper(verb[:1]) + verb[1:] + " a sandbox",
		Long:  strings.ToUpper(verb[:1]) + verb[1:] + " a sandbox.\nExample:\n  daytona sandbox " + verb + " <id>",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			timeout, err := workload.ParseTimeout(a.Globals.Timeout)
			if err != nil {
				return err
			}
			if verb == "delete" {
				if err := a.PreflightConfirm(verb, "sandbox "+args[0]); err != nil {
					return err
				}
			}
			home, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			cache := workload.NewCatalogCache()
			resolved, err := workload.ResolveSandbox(cmd.Context(), home, cache, args[0])
			if err != nil {
				return err
			}
			result, waitID, waitFor, err := run(cmd, resolved)
			if err != nil {
				return err
			}
			waitOptions := workload.SandboxWaitOptions{
				ID:      waitID,
				Target:  waitFor,
				Timeout: timeout,
			}
			if verb == "start" {
				waitOptions.AllowInitialStopped = true
			}
			if verb == "pause" && resolved.Detail.Class == workload.ClassMacOS {
				waitOptions.RunningAgainFails = true
			}
			if verb == "fork" && resolved.Detail.Class == workload.ClassMacOS {
				waitOptions.StoppedPassed = true
			}
			if a.Globals.NoWait || waitID == "" || waitFor == "" {
				displayID := args[0]
				if waitID != "" {
					displayID = waitID
				}
				return a.Printer().Object(result, [][2]string{{"id", displayID}, {"action", verb}})
			}
			if waitFor == "deleted" {
				detail, waitErr := workload.WaitForSandbox(cmd.Context(), resolved.Client, waitOptions)
				if waitErr != nil {
					return waitErr
				}
				return a.Printer().Object(detail, sandboxDetailKV(detail))
			}
			final, err := workload.WaitForSandbox(cmd.Context(), resolved.Client, waitOptions)
			if err != nil {
				return err
			}
			return a.Printer().Object(final, sandboxDetailKV(final))
		},
	}
}

func sandboxJobAction[T any](cmd *cobra.Command, resolved workload.ResolvedSandbox, verb string) (any, string, string, error) {
	method := http.MethodPost
	path := fmt.Sprintf("/sandboxes/%s/%s", url.PathEscape(resolved.Detail.Id), verb)
	waitFor := map[string]string{"start": "running", "stop": "stopped", "pause": "paused", "resume": "running"}[verb]
	if verb == "delete" {
		method = http.MethodDelete
		path = fmt.Sprintf("/sandboxes/%s", url.PathEscape(resolved.Detail.Id))
		waitFor = "deleted"
	}
	var resp T
	if err := resolved.Client.Do(cmd.Context(), method, path, nil, nil, &resp); err != nil {
		return nil, "", "", err
	}
	return resp, resolved.Detail.Id, waitFor, nil
}

func hasLabelUpdate(cmd *cobra.Command, fileBody input.Body, flags sandboxUpdateFlags) bool {
	if cmd.Flags().Changed("label") || flags.clearLabels {
		return true
	}
	_, ok := fileBody["labels"]
	return ok
}

func hasLifecycleUpdate(cmd *cobra.Command, fileBody input.Body) bool {
	for _, name := range []string{"timeout", "on-timeout", "idle-timeout", "on-idle-timeout"} {
		if cmd.Flags().Changed(name) {
			return true
		}
	}
	_, ok := fileBody["lifecycle_policy"]
	return ok
}

func buildSandboxLabelsBody(cmd *cobra.Command, fileBody input.Body, flags sandboxUpdateFlags) (gen.SetSandboxLabelsRequest, error) {
	body := input.Body{}
	if existing, ok := fileBody["labels"]; ok {
		body["labels"] = existing
	}
	if flags.clearLabels {
		empty := map[string]string{}
		body["labels"] = empty
	}
	if cmd.Flags().Changed("label") {
		labels, err := input.KV(flags.labels, "--label")
		if err != nil {
			return gen.SetSandboxLabelsRequest{}, err
		}
		body["labels"] = labels
	}
	if err := input.RejectNulls(body, "labels"); err != nil {
		return gen.SetSandboxLabelsRequest{}, err
	}
	return workload.DecodeBody[gen.SetSandboxLabelsRequest](body)
}

func buildSandboxLifecycleBody(cmd *cobra.Command, fileBody input.Body, flags sandboxUpdateFlags) (gen.UpdateSandboxLifecyclePolicyRequest, error) {
	hasOverride := false
	for _, name := range []string{"timeout", "on-timeout", "idle-timeout", "on-idle-timeout"} {
		if cmd.Flags().Changed(name) {
			hasOverride = true
			break
		}
	}
	if existing, ok := fileBody["lifecycle_policy"]; ok && !hasOverride {
		if existing == nil {
			return gen.UpdateSandboxLifecyclePolicyRequest{}, cerr.Usage("\"lifecycle_policy\" is not nullable; omit it to keep the current value")
		}
		switch existing.(type) {
		case map[string]any, input.Body:
		default:
			return gen.UpdateSandboxLifecyclePolicyRequest{}, cerr.Usage("\"lifecycle_policy\" must be an object")
		}
	}
	body := input.Body{}
	if existing, ok := fileBody["lifecycle_policy"].(map[string]any); ok {
		for key, value := range existing {
			body[key] = value
		}
	}
	if existing, ok := fileBody["lifecycle_policy"].(input.Body); ok {
		for key, value := range existing {
			body[key] = value
		}
	}
	workload.SetIfChanged(body, cmd.Flags(), "timeout", "timeout_seconds", flags.timeoutSecs)
	workload.SetIfChanged(body, cmd.Flags(), "on-timeout", "on_timeout", flags.onTimeout)
	workload.SetIfChanged(body, cmd.Flags(), "idle-timeout", "idle_expiry_seconds", flags.idleTimeoutSecs)
	workload.SetIfChanged(body, cmd.Flags(), "on-idle-timeout", "on_idle_timeout", flags.onIdleTimeout)
	if err := input.RejectNulls(body, "timeout_seconds", "on_timeout", "idle_expiry_seconds", "on_idle_timeout"); err != nil {
		return gen.UpdateSandboxLifecyclePolicyRequest{}, err
	}
	return workload.DecodeBody[gen.UpdateSandboxLifecyclePolicyRequest](body)
}

func validateSandboxLifecycleUpdate(class string, lifecycle gen.UpdateSandboxLifecyclePolicyRequest) error {
	if class == workload.ClassLinux {
		return nil
	}
	pauses := (lifecycle.OnTimeout != nil && *lifecycle.OnTimeout == gen.Pause) ||
		(lifecycle.OnIdleTimeout != nil && *lifecycle.OnIdleTimeout == gen.Pause)
	if pauses {
		return cerr.Usage("%s sandboxes do not support the pause lifecycle action", class)
	}
	return nil
}
