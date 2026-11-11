// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"errors"
	"net/url"
	"strconv"
	"time"

	"github.com/spf13/cobra"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	toolboxclient "github.com/daytona/clients/cli/internal/toolbox"
)

func newSandboxProcessSubcommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "process",
		Short: "Inspect and control sandbox processes",
		Long:  "Lists, gets, waits for, signals, kills, logs, and attaches to sandbox processes.",
	}
	cmd.AddCommand(newSandboxProcessListCommand(), newSandboxProcessGetCommand(), newSandboxProcessLogsCommand(), newSandboxProcessWaitCommand(), newSandboxProcessSignalCommand(), newSandboxProcessKillCommand(), newSandboxProcessAttachCommand())
	return cmd
}

func newSandboxProcessListCommand() *cobra.Command {
	var state, kind, name, sessionID string
	var pid int
	cmd := &cobra.Command{
		Use:   "list <sandbox-id>",
		Short: "List processes in a sandbox",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			toolClient, _, _, err := sandboxToolboxClient(cmd.Context(), a, args[0])
			if err != nil {
				return err
			}
			query := url.Values{}
			if state != "" {
				query.Set("state", state)
			}
			if kind != "" {
				query.Set("kind", kind)
			}
			if name != "" {
				query.Set("name", name)
			}
			if sessionID != "" {
				query.Set("sessionId", sessionID)
			}
			if pid > 0 {
				query.Set("pid", strconv.Itoa(pid))
			}
			items, err := toolClient.ListProcesses(cmd.Context(), query)
			if err != nil {
				return err
			}
			return printToolboxList(a, output.List[api.Raw[tbgen.ProcessDto]]{Items: items}, func(items []tbgen.ProcessDto) output.Table {
				rows := make([][]string, 0, len(items))
				for _, item := range items {
					pidValue := ""
					if item.Pid != nil {
						pidValue = strconv.Itoa(int(*item.Pid))
					}
					rows = append(rows, []string{item.Id, string(item.Kind), string(item.State), pidValue, derefString(item.Name)})
				}
				return output.Table{Headers: []string{"ID", "KIND", "STATE", "PID", "NAME"}, Rows: rows}
			})
		},
	}
	cmd.Flags().StringVar(&state, "state", "", "filter by running|terminal|all")
	cmd.Flags().StringVar(&kind, "kind", "", "filter by exec|pty|code|session")
	cmd.Flags().StringVar(&name, "name", "", "filter by live name")
	cmd.Flags().StringVar(&sessionID, "session-id", "", "filter by session id")
	cmd.Flags().IntVar(&pid, "pid", 0, "filter by OS pid")
	return cmd
}

func newSandboxProcessGetCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "get <sandbox-id> <process-id>",
		Short: "Fetch one process",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			toolClient, _, _, err := sandboxToolboxClient(cmd.Context(), a, args[0])
			if err != nil {
				return err
			}
			proc, err := toolClient.GetProcess(cmd.Context(), args[1])
			if err != nil {
				return err
			}
			return printToolboxObject(a, proc, processKV(proc.V))
		},
	}
}

func newSandboxProcessWaitCommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "wait <sandbox-id> <process-id>",
		Short: "Wait for a process to exit",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			timeout, err := sandboxTimeout(a)
			if err != nil {
				return err
			}
			waitCtx, cancel := newFollowProcessContext(cmd.Context(), timeout)
			defer cancel()
			toolClient, _, _, err := sandboxToolboxClient(waitCtx, a, args[0])
			if err != nil {
				return err
			}
			result, err := toolClient.WaitProcess(waitCtx, args[1], timeout)
			if err != nil {
				// The deadline context expiring inside the transport reads as
				// a cancellation; it is the documented timeout (exit 8).
				if timeout > 0 && errors.Is(waitCtx.Err(), context.DeadlineExceeded) && cmd.Context().Err() == nil {
					return cerr.New(cerr.ExitTimeout, "timed out after %s waiting for process %s", timeout, args[1])
				}
				return err
			}
			if err := printToolboxObject(a, result, exitResultKV(args[1], result.V)); err != nil {
				return err
			}
			return exitError(result.V)
		},
	}
	addTimeoutFlag(cmd, defaultWaitTimeout, "how long to wait for the process to exit (Go duration)")
	return cmd
}

func newSandboxProcessSignalCommand() *cobra.Command {
	var escalateAfter string
	var escalateTo string
	cmd := &cobra.Command{
		Use:   "signal <sandbox-id> <process-id> <signal>",
		Short: "Send a signal to a process",
		Args:  cobra.ExactArgs(3),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			signalName, err := toolboxclient.ValidateSignalName(args[2])
			if err != nil {
				return cerr.Usage("%v", err)
			}
			var after time.Duration
			if escalateAfter != "" {
				after, err = time.ParseDuration(escalateAfter)
				if err != nil {
					return cerr.Usage("invalid --escalate-after %q: %v", escalateAfter, err)
				}
				if after > 10*time.Minute {
					return cerr.Usage("--escalate-after must be at most 10m")
				}
				if after <= 0 {
					return cerr.Usage("--escalate-after must be greater than zero")
				}
			}
			if escalateTo != "" {
				escalateTo, err = toolboxclient.ValidateSignalName(escalateTo)
				if err != nil {
					return cerr.Usage("%v", err)
				}
			}
			toolClient, _, _, err := sandboxToolboxClient(cmd.Context(), a, args[0])
			if err != nil {
				return err
			}
			if err := toolClient.SignalProcess(cmd.Context(), args[1], signalName, after, escalateTo); err != nil {
				return err
			}
			return a.Printer().Object(map[string]any{"id": args[1], "signal": signalName}, [][2]string{{"id", args[1]}, {"signal", signalName}})
		},
	}
	cmd.Flags().StringVar(&escalateAfter, "escalate-after", "", "escalate after this duration (Go duration, e.g. 5s)")
	flagPlaceholder(cmd.Flags(), "escalate-after", "DURATION")
	cmd.Flags().StringVar(&escalateTo, "escalate-to", "", "signal to escalate to, e.g. KILL")
	return cmd
}

func newSandboxProcessKillCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "kill <sandbox-id> <process-id>",
		Short: "Send SIGKILL to a process",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			toolClient, _, _, err := sandboxToolboxClient(cmd.Context(), a, args[0])
			if err != nil {
				return err
			}
			if err := toolClient.SignalProcess(cmd.Context(), args[1], "KILL", 0, ""); err != nil {
				return err
			}
			return a.Printer().Object(map[string]any{"id": args[1], "signal": "KILL"}, [][2]string{{"id", args[1]}, {"signal", "KILL"}})
		},
	}
}

func newSandboxProcessAttachCommand() *cobra.Command {
	var cursor string
	cmd := &cobra.Command{
		Use:   "attach <sandbox-id> <process-id>",
		Short: "Attach to a process websocket stream",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			toolClient, _, _, err := sandboxToolboxClient(cmd.Context(), a, args[0])
			if err != nil {
				return err
			}
			proc, err := toolClient.GetProcess(cmd.Context(), args[1])
			if err != nil {
				return err
			}
			result, err := toolClient.AttachTerminal(cmd.Context(), toolboxclient.ProcessAttachment{ID: proc.V.Id, Cursor: cursor})
			if err != nil {
				return err
			}
			return exitError(result)
		},
	}
	cmd.Flags().StringVar(&cursor, "cursor", "", "replay from this cursor before the live stream")
	return cmd
}

func derefString(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

// exitResultKV is the table form of a process wait result.
func exitResultKV(processID string, result tbgen.ExitResult) [][2]string {
	kv := [][2]string{{"id", processID}}
	if result.Reason != nil {
		kv = append(kv, [2]string{"exit_reason", string(*result.Reason)})
	}
	if result.ExitCode != nil {
		kv = append(kv, [2]string{"exit_code", strconv.Itoa(int(*result.ExitCode))})
	}
	if result.Signal != nil {
		kv = append(kv, [2]string{"signal", *result.Signal})
	}
	return kv
}
