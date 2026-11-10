// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"strings"

	"github.com/daytona/clients/cli/internal/cerr"
	toolboxclient "github.com/daytona/clients/cli/internal/toolbox"
	"github.com/spf13/cobra"
)

func newSandboxSSHSubcommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "ssh [flags] <sandbox-id> [ssh-args...] [command]",
		Short: "Open SSH through the sandbox gateway",
		Long:  "Execs the system ssh binary against the sandbox SSH gateway. CLI flags go before the sandbox id; everything after it is passed to ssh. Extra SSH flags are forwarded before the destination; any remaining arguments become the remote command.",
		Args:  cobra.ArbitraryArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) == 0 {
				return cerr.Usage("sandbox ssh needs a sandbox id")
			}
			a := FromContext(cmd.Context())
			toolClient, target, apiClient, err := sandboxToolboxClient(cmd.Context(), a, args[0])
			if err != nil {
				return err
			}
			if err := toolboxclient.ResolveSSHGateway(cmd.Context(), apiClient, target); err != nil {
				return err
			}
			if target.SSHHost == "" || target.SSHPort == 0 {
				return cerr.New(cerr.ExitConflict, "sandbox %s has no SSH gateway", args[0])
			}
			sshBinary, err := exec.LookPath("ssh")
			if err != nil {
				return cerr.Usage("ssh is not installed or not on PATH").WithHint("install OpenSSH and retry")
			}
			sshArgs, remoteCommand, err := splitSSHArguments(args[1:])
			if err != nil {
				return err
			}
			invocation, err := toolboxclient.BuildSSHInvocation(
				toolboxclient.SSHTarget{Host: target.SSHHost, Port: target.SSHPort, SandboxID: target.Detail.Id, Credential: toolClient.Credential()},
				toolboxclient.SSHCommand{Options: sshArgs, Args: remoteCommand},
			)
			if err != nil {
				return err
			}
			defer invocation.Cleanup()
			if err := runSSHCommand(cmd.Context(), sshBinary, invocation.Args); err != nil {
				var exitErr *exec.ExitError
				if errors.As(err, &exitErr) {
					return cerr.New(exitErr.ExitCode(), "ssh exited with code %d", exitErr.ExitCode()).WithSource(cerr.SourceSSH)
				}
				return err
			}
			return nil
		},
	}
	cmd.Flags().SetInterspersed(false)
	return cmd
}

func splitSSHArguments(args []string) ([]string, []string, error) {
	if len(args) == 0 {
		return nil, nil, nil
	}
	sshArgs := make([]string, 0, len(args))
	index := 0
	for index < len(args) {
		arg := args[index]
		if arg == "--" {
			index++
			break
		}
		if !strings.HasPrefix(arg, "-") || arg == "-" {
			break
		}
		sshArgs = append(sshArgs, arg)
		if optionNeedsValue(arg) {
			index++
			if index >= len(args) {
				return nil, nil, cerr.Usage("ssh option %s needs a value", arg)
			}
			sshArgs = append(sshArgs, args[index])
		}
		index++
	}
	return sshArgs, args[index:], nil
}

func optionNeedsValue(arg string) bool {
	if strings.HasPrefix(arg, "--") {
		return false
	}
	if len(arg) > 2 {
		return false
	}
	switch arg {
	case "-B", "-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J", "-L", "-l", "-m", "-O", "-o", "-P", "-p", "-Q", "-R", "-S", "-W", "-w":
		return true
	default:
		return false
	}
}

var runSSHCommand = func(ctx context.Context, binary string, args []string) error {
	cmd := exec.CommandContext(ctx, binary, args...)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}
