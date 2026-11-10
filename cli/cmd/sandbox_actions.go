// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"fmt"
	"net/http"
	"net/url"
	"os"
	"strings"

	"github.com/daytona/clients/cli/internal/api"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/input"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

func newSandboxStartCommand() *cobra.Command {
	return newSandboxActionCommand("start", func(cmd *cobra.Command, resolved workload.ResolvedSandbox) (any, string, string, error) {
		// State-aware, as the SDKs' start(): a running sandbox needs nothing
		// and a paused one is resumed.
		switch resolved.Detail.Status {
		case "running":
			return nil, resolved.Detail.Id, "running", nil
		case "paused":
			return sandboxJobAction[gen.AsyncOperation](cmd, resolved, "resume")
		}
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
		var resp api.Raw[gen.ForkSandboxOperation]
		if err := resolved.Client.Do(cmd.Context(), http.MethodPost, fmt.Sprintf("/sandboxes/%s/fork", url.PathEscape(resolved.Detail.Id)), nil, body, &resp); err != nil {
			return nil, "", "", err
		}
		// A macOS fork starts running only if its source is running when the
		// fork is created. The source can change after our lookup, so accept
		// either a running or stopped fork (StoppedSettles).
		waitFor := "paused"
		if macos || resolved.Detail.Status == "running" {
			waitFor = "running"
		}
		return resp, resp.V.Id, waitFor, nil
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
	labels         []string
	clearLabels    bool
	lifecycleTTL   secondsFlag
	onLifecycleTTL string
	idleTimeout    secondsFlag
	onIdleTimeout  string
}

// sandboxUpdateFileKeys are the only top-level keys of `sandbox update -f`:
// the body is the CLI's composite of the labels and lifecycle-policy
// requests, not either wire body.
var sandboxUpdateFileKeys = []string{"labels", "lifecycle_policy"}

var sandboxLifecycleFlagNames = []string{"lifecycle-timeout", "on-lifecycle-timeout", "idle-timeout", "on-idle-timeout"}

func newSandboxUpdateCommand() *cobra.Command {
	flags := &sandboxUpdateFlags{}
	cmd := &cobra.Command{
		Use:   "update <id>",
		Short: "Update sandbox labels or lifecycle",
		Long: "Update sandbox labels or lifecycle policy. Labels are replaced, not merged, except the name label:\n" +
			"labels.name (stamped by a named create) is kept unless the new set names it, and then it must match the sandbox name.\n" +
			"--file takes {\"labels\": {...}, \"lifecycle_policy\": {...}}; any other top-level key is rejected.\n" +
			"Examples:\n  daytona sandbox update <id> --label env=dev\n  daytona sandbox update <id> --idle-timeout 30m --on-idle-timeout delete\n  daytona sandbox update <id> --idle-timeout off",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			fileBody, err := input.Load(a.Globals.File, os.Stdin, false)
			if err != nil {
				return err
			}
			if err := rejectUnknownKeys(fileBody, "--file", sandboxUpdateFileKeys...); err != nil {
				return err
			}
			labelUpdate := hasLabelUpdate(cmd, fileBody, *flags)
			lifecycleUpdate := hasLifecycleUpdate(cmd, fileBody)
			if !labelUpdate && !lifecycleUpdate {
				return cerr.Usage("nothing to update").WithHint("pass --label, --clear-labels, lifecycle flags, or --file")
			}
			var labelsBody gen.SetSandboxLabelsRequest
			if labelUpdate {
				labelsBody, err = buildSandboxLabelsBody(cmd, fileBody, *flags)
				if err != nil {
					return err
				}
			}
			var lifecycleBody gen.UpdateSandboxLifecyclePolicyRequest
			if lifecycleUpdate {
				lifecycleBody, err = buildSandboxLifecycleBody(cmd, fileBody, *flags)
				if err != nil {
					return err
				}
			}
			home, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			resolved, err := workload.ResolveSandbox(cmd.Context(), home, workload.NewCatalogCache(), args[0])
			if err != nil {
				return err
			}
			if lifecycleUpdate {
				persistent := resolved.Detail.Persistent != nil && *resolved.Detail.Persistent
				if err := validateLifecycleActions(string(resolved.Detail.Class), persistent, lifecycleBody); err != nil {
					return err
				}
			}
			if labelUpdate {
				if err := keepNameLabel(&labelsBody, resolved.Detail); err != nil {
					return err
				}
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
	f := cmd.Flags()
	f.StringArrayVar(&flags.labels, "label", nil, "replacement label KEY=VALUE (repeatable)")
	f.BoolVar(&flags.clearLabels, "clear-labels", false, "remove every label except the name label")
	addSecondsFlag(f, &flags.lifecycleTTL, "lifecycle-timeout", "", "wall-clock lifetime (Go duration, e.g. 2h) until --on-lifecycle-timeout fires; restarts the countdown from now")
	f.StringVar(&flags.onLifecycleTTL, "on-lifecycle-timeout", "", "action when --lifecycle-timeout elapses: delete, or stop on a persistent macOS sandbox")
	flags.idleTimeout.keywords = map[string]int64{"off": -1}
	flags.idleTimeout.min = minIdleSeconds
	addSecondsFlag(f, &flags.idleTimeout, "idle-timeout", "", "idle time (Go duration, at least 1m) until --on-idle-timeout fires; off clears the idle expiry")
	f.StringVar(&flags.onIdleTimeout, "on-idle-timeout", "", "action when --idle-timeout elapses: delete, or stop on a persistent macOS sandbox")
	flagPlaceholder(f, "on-lifecycle-timeout", "ACTION")
	flagPlaceholder(f, "on-idle-timeout", "ACTION")
	addFileFlag(cmd, `JSON body {"labels": {...}, "lifecycle_policy": {...}} from a file (or - for stdin); explicit flags override its fields`)
	return cmd
}

func newSandboxActionCommand(verb string, run func(cmd *cobra.Command, resolved workload.ResolvedSandbox) (any, string, string, error)) *cobra.Command {
	cmd := &cobra.Command{
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
				waitOptions.StoppedSettles = true
			}
			if a.Globals.NoWait || waitID == "" || waitFor == "" {
				return printSandboxAfterAction(cmd, resolved, result, waitID, verb)
			}
			if waitFor == "deleted" {
				detail, waitErr := workload.WaitForSandbox(cmd.Context(), resolved.Client, waitOptions)
				if waitErr != nil {
					return waitErr
				}
				return a.Printer().Object(detail, sandboxDetailKV(detail.V))
			}
			final, err := workload.WaitForSandbox(cmd.Context(), resolved.Client, waitOptions)
			if err != nil {
				return err
			}
			return a.Printer().Object(final, sandboxDetailKV(final.V))
		},
	}
	addWaitFlags(cmd)
	return cmd
}

// printSandboxAfterAction prints, without waiting, the sandbox the action
// acted on (the fork, for fork) as `sandbox get` does, so --no-wait prints the
// same shape as a waited action. A sandbox the delete already removed (404)
// prints the API's acknowledgement, whose id and status are detail fields.
func printSandboxAfterAction(cmd *cobra.Command, resolved workload.ResolvedSandbox, ack any, id, verb string) error {
	a := FromContext(cmd.Context())
	if id == "" {
		id = resolved.Detail.Id
	}
	var detail api.Raw[gen.Sandbox]
	err := resolved.Client.Do(cmd.Context(), http.MethodGet, fmt.Sprintf("/sandboxes/%s", url.PathEscape(id)), nil, nil, &detail)
	if err != nil {
		if verb == "delete" && cerr.ExitCode(err) == cerr.ExitNotFound && ack != nil {
			return a.Printer().Object(ack, [][2]string{{"id", id}, {"action", verb}})
		}
		return err
	}
	return a.Printer().Object(detail, sandboxDetailKV(detail.V))
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
	var resp api.Raw[T]
	if err := resolved.Client.Do(cmd.Context(), method, path, nil, nil, &resp); err != nil {
		return nil, "", "", err
	}
	return resp, resolved.Detail.Id, waitFor, nil
}

// keepNameLabel carries the sandbox's labels["name"] into a replacement label
// set that does not name it, so SDK find_by_name and label searches keep
// finding the sandbox. A set that names it must agree with the sandbox name.
func keepNameLabel(body *gen.SetSandboxLabelsRequest, detail gen.Sandbox) error {
	labels := map[string]string{}
	if body.Labels != nil {
		for k, v := range *body.Labels {
			labels[k] = v
		}
	}
	if given, ok := labels[nameLabel]; ok {
		if detail.Name != "" && given != detail.Name {
			return cerr.Usage("label %q (%s) must match the sandbox name (%s)", nameLabel, given, detail.Name)
		}
	} else if detail.Labels != nil {
		if current, ok := (*detail.Labels)[nameLabel]; ok {
			labels[nameLabel] = current
		}
	}
	body.Labels = &labels
	return nil
}

func hasLabelUpdate(cmd *cobra.Command, fileBody input.Body, flags sandboxUpdateFlags) bool {
	if cmd.Flags().Changed("label") || flags.clearLabels {
		return true
	}
	_, ok := fileBody["labels"]
	return ok
}

func hasLifecycleUpdate(cmd *cobra.Command, fileBody input.Body) bool {
	for _, name := range sandboxLifecycleFlagNames {
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
	for _, name := range sandboxLifecycleFlagNames {
		if cmd.Flags().Changed(name) {
			hasOverride = true
			break
		}
	}
	// A null policy is replaced by the lifecycle flags when there are any; a
	// value that is not an object is malformed either way.
	if existing, ok := fileBody["lifecycle_policy"]; ok {
		switch existing.(type) {
		case nil:
			if !hasOverride {
				return gen.UpdateSandboxLifecyclePolicyRequest{}, cerr.Usage("\"lifecycle_policy\" is not nullable; omit it to keep the current value")
			}
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
	workload.SetIfChanged(body, cmd.Flags(), "lifecycle-timeout", "timeout_seconds", flags.lifecycleTTL.seconds)
	workload.SetIfChanged(body, cmd.Flags(), "on-lifecycle-timeout", "on_timeout", flags.onLifecycleTTL)
	workload.SetIfChanged(body, cmd.Flags(), "idle-timeout", "idle_expiry_seconds", flags.idleTimeout.seconds)
	workload.SetIfChanged(body, cmd.Flags(), "on-idle-timeout", "on_idle_timeout", flags.onIdleTimeout)
	if err := input.RejectNulls(body, "timeout_seconds", "on_timeout", "idle_expiry_seconds", "on_idle_timeout"); err != nil {
		return gen.UpdateSandboxLifecyclePolicyRequest{}, err
	}
	return workload.DecodeBody[gen.UpdateSandboxLifecyclePolicyRequest](body)
}

// rejectUnknownKeys refuses a top-level key outside allowed. Keys match
// exactly: a differently cased key would otherwise be dropped silently.
func rejectUnknownKeys(body input.Body, what string, allowed ...string) error {
	for key := range body {
		known := false
		for _, a := range allowed {
			if key == a {
				known = true
				break
			}
		}
		if !known {
			return cerr.Usage("%s: unknown field %q (allowed: %s)", what, key, strings.Join(allowed, ", "))
		}
	}
	return nil
}
