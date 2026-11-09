// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"errors"
	"io"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"golang.org/x/term"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/cerr"
	toolboxclient "github.com/daytona/clients/cli/internal/toolbox"
)

func newSandboxSSHSubcommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:                "ssh <sandbox-id> [ssh-args...] [command]",
		Short:              "Open SSH through the sandbox gateway",
		Long:               "Execs the system ssh binary against the sandbox SSH gateway. Extra SSH flags are forwarded before the destination; any remaining arguments become the remote command.",
		DisableFlagParsing: true,
		Args:               cobra.ArbitraryArgs,
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
			invocation, err := toolboxclient.BuildSSHInvocation(target.SSHHost, target.SSHPort, target.Detail.Id, toolClient.Credential(), sshArgs, remoteCommand)
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

func runTTYExec(ctx context.Context, toolClient *toolboxclient.Client, argv []string, cwd string, env map[string]string, user string) error {
	cols, rows, err := term.GetSize(int(os.Stdout.Fd()))
	if err != nil {
		cols, rows = 80, 24
	}
	request := tbgen.CreateProcessRequest{
		Argv:     &argv,
		Kind:     kindPtr(tbgen.Pty),
		Stdin:    stdinModePtr(tbgen.Open),
		KeepLogs: keepLogsPtr(tbgen.KeepLogs("until_cleanup")),
		Terminal: &tbgen.TerminalSize{Cols: int32(cols), Rows: int32(rows)},
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
	proc, err := toolClient.CreateProcess(ctx, request, nil)
	if err != nil {
		return err
	}
	return interactiveAttachAndWait(ctx, toolClient, proc.Id, "")
}

func interactiveAttachAndWait(ctx context.Context, toolClient *toolboxclient.Client, processID, cursor string) error {
	waitCtx := ctx
	ws, err := toolClient.AttachWebSocket(ctx, processID, cursor)
	if err != nil {
		return err
	}
	defer ws.Close()
	fd := int(os.Stdin.Fd())
	state, rawErr := term.MakeRaw(fd)
	if rawErr == nil {
		defer term.Restore(fd, state)
	}
	inputCtx, cancelInput := context.WithCancel(ctx)
	defer cancelInput()
	wsClosed := make(chan struct{})
	defer close(wsClosed)
	go func() {
		select {
		case <-inputCtx.Done():
			_ = ws.Close()
		case <-wsClosed:
		}
	}()
	inputErrCh := make(chan error, 1)
	inputDone := make(chan struct{})
	waitForInput := func() error {
		select {
		case inputErr := <-inputErrCh:
			return inputErr
		case <-inputDone:
			return nil
		case <-time.After(50 * time.Millisecond):
			return nil
		}
	}
	go func() {
		defer close(inputDone)
		buf := make([]byte, 32*1024)
		for {
			n, readErr := os.Stdin.Read(buf)
			if n > 0 {
				if writeErr := ws.WriteBinary(buf[:n]); writeErr != nil {
					inputErrCh <- writeErr
					return
				}
			}
			if errors.Is(readErr, io.EOF) {
				inputErrCh <- nil
				return
			}
			if readErr != nil {
				inputErrCh <- readErr
				return
			}
			select {
			case <-inputCtx.Done():
				inputErrCh <- nil
				return
			default:
			}
		}
	}()
	for {
		select {
		case inputErr := <-inputErrCh:
			if inputErr != nil {
				return inputErr
			}
		case <-waitCtx.Done():
			if inputErr := waitForInput(); inputErr != nil {
				return inputErr
			}
			return waitCtx.Err()
		default:
		}
		msg, readErr := ws.ReadMessage()
		if errors.Is(readErr, io.EOF) {
			break
		}
		if readErr != nil {
			if waitCtx.Err() != nil {
				if inputErr := waitForInput(); inputErr != nil {
					return inputErr
				}
				return waitCtx.Err()
			}
			return readErr
		}
		if _, writeErr := os.Stdout.Write(msg); writeErr != nil {
			return writeErr
		}
	}
	cancelInput()
	if inputErr := waitForInput(); inputErr != nil {
		return inputErr
	}
	result, err := toolClient.WaitProcess(waitCtx, processID, 0)
	if err != nil {
		if waitCtx.Err() != nil {
			return waitCtx.Err()
		}
		return err
	}
	return exitError(result.V)
}

func kindPtr(v tbgen.Kind) *tbgen.Kind             { return &v }
func keepLogsPtr(v tbgen.KeepLogs) *tbgen.KeepLogs { return &v }
