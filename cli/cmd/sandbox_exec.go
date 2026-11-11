// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"errors"
	"os"
	"time"

	"github.com/spf13/cobra"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/input"
	toolboxclient "github.com/daytona/clients/cli/internal/toolbox"
	"golang.org/x/term"
)

func init() {
	Register(newSandboxCommand)
}

func newSandboxCommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:     "sandbox",
		Short:   "Create and operate sandboxes",
		Long:    "Create sandboxes, inspect their lifecycle, and run commands inside them.",
		GroupID: GroupWorkload,
	}
	cmd.AddCommand(
		newSandboxCreateCommand(),
		newSandboxCreateFromSnapshotCommand(),
		newSandboxCreateMacosCommand(),
		newSandboxSearchCommand(),
		newSandboxListCommand(),
		newSandboxGetCommand(),
		newSandboxWaitCommand(),
		newSandboxStartCommand(),
		newSandboxStopCommand(),
		newSandboxPauseCommand(),
		newSandboxResumeCommand(),
		newSandboxForkCommand(),
		newSandboxDeleteCommand(),
		newSandboxUpdateCommand(),
		newSandboxLogsCommand(),
		newSandboxUsageCommand(),
		newSandboxExecSubcommand(),
		newSandboxSSHSubcommand(),
		newSandboxCPSubcommand(),
		newSandboxDockerSubcommand(),
		newSandboxProcessSubcommand(),
		newSandboxPortSubcommand(),
	)
	return cmd
}

func newSandboxExecSubcommand() *cobra.Command {
	var cwd string
	var envPairs []string
	var tty bool
	var user string
	var executionTimeout time.Duration
	cmd := &cobra.Command{
		Use:   "exec <sandbox-id> -- <command> [args...]",
		Short: "Run a command inside a sandbox",
		Long:  "Starts a sandbox process, streams its bytes, and exits with the process exit code (127 if the command cannot start). Interrupting the CLI signals the process with INT, escalating to KILL after 1s. --timeout only limits the client wait; --execution-timeout limits remote execution.\n\nExamples:\n  daytona sandbox exec sbx-123 -- python -V\n  printf hi | daytona sandbox exec sbx-123 -- cat\n  daytona sandbox exec sbx-123 --tty -- bash",
		Args:  cobra.ArbitraryArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) < 2 {
				return cerr.Usage("sandbox exec needs a sandbox id and a command after --")
			}
			if cmd.Flags().Changed("execution-timeout") && executionTimeout <= 0 {
				return cerr.Usage("--execution-timeout must be greater than zero")
			}
			a := FromContext(cmd.Context())
			env, err := input.KV(envPairs, "--env")
			if err != nil {
				return err
			}
			timeout, err := sandboxTimeout(a)
			if err != nil {
				return err
			}
			execCtx := cmd.Context()
			cancel := func() {}
			if timeout > 0 {
				var deadlineCancel context.CancelFunc
				execCtx, deadlineCancel = context.WithTimeout(execCtx, timeout)
				cancel = deadlineCancel
			}
			defer cancel()
			toolClient, _, _, err := sandboxToolboxClient(execCtx, a, args[0])
			if err != nil {
				return timeoutForDeadline(execCtx, timeout, err)
			}
			argv := args[1:]
			request := tbgen.CreateProcessRequest{Argv: &argv}
			if executionTimeout > 0 {
				ms := executionTimeout.Milliseconds()
				if executionTimeout%time.Millisecond != 0 {
					ms++ // a positive sub-millisecond budget must not become zero
				}
				request.TimeoutMs = &ms
			}
			if cwd != "" {
				request.Cwd = &cwd
			}
			if len(env) > 0 {
				request.Env = &env
			}
			if user != "" {
				request.User = &user
			}
			var result toolboxclient.ProcessResult
			if tty {
				if !term.IsTerminal(int(os.Stdin.Fd())) || !term.IsTerminal(int(os.Stdout.Fd())) {
					return cerr.Usage("--tty requires a real terminal on stdin and stdout")
				}
				result, err = toolClient.StreamTerminal(execCtx, request)
			} else {
				stdin := processStdinReader()
				request.Stdin = stdinModePtr(tbgen.Closed)
				if stdin != nil {
					request.Stdin = stdinModePtr(tbgen.Open)
				}
				result, err = toolClient.StreamProcess(execCtx, toolboxclient.StreamProcessOptions{
					Request: request, Timeout: timeout, Stdout: a.Stdout, Stderr: a.Stderr,
					Stdin: stdin, LogEncoding: "base64",
				})
			}
			if errors.Is(cmd.Context().Err(), context.Canceled) && !errors.Is(execCtx.Err(), context.DeadlineExceeded) {
				toolClient.InterruptProcess(cmd.Context(), result.Process.Id)
				return cerr.FromContext(cmd.Context())
			}
			if err != nil {
				return timeoutForDeadline(execCtx, timeout, err)
			}
			return exitError(result.Exit)
		},
	}
	cmd.Flags().StringVar(&cwd, "cwd", "", "working directory inside the sandbox")
	cmd.Flags().StringArrayVar(&envPairs, "env", nil, "environment variable to set (KEY=VALUE, repeatable)")
	cmd.Flags().BoolVar(&tty, "tty", false, "run the command in a PTY and attach interactively")
	cmd.Flags().StringVar(&user, "user", "", "sandbox OS user to run the command as")
	cmd.Flags().DurationVar(&executionTimeout, "execution-timeout", 0, "server-side execution deadline (positive Go duration, rounded up to milliseconds)")
	addTimeoutFlag(cmd, defaultWaitTimeout, "how long the command may run before the CLI stops waiting (Go duration; exit 8)")
	return cmd
}

func timeoutForDeadline(ctx context.Context, timeout time.Duration, err error) error {
	if err == nil {
		return nil
	}
	if timeout > 0 && (errors.Is(ctx.Err(), context.DeadlineExceeded) || errors.Is(err, context.DeadlineExceeded)) {
		return cerr.New(cerr.ExitTimeout, "timed out after %s waiting for the process to finish", timeout)
	}
	var detail cerr.Detailed
	if errors.As(err, &detail) && detail.ErrorCode() == "PROCESS_START_FAILED" {
		return cerr.Wrap(127, err, "%s", err.Error()).WithSource(cerr.SourceProcess)
	}
	if errors.Is(ctx.Err(), context.Canceled) {
		return cerr.FromContext(ctx)
	}
	return err
}
