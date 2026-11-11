// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"os/exec"
	"strings"
	"sync"
	"syscall"
	"time"

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
			sshArgs, remoteCommand, err := splitSSHArguments(args[1:])
			if err != nil {
				return err
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
			invocation, err := toolboxclient.BuildSSHInvocation(
				toolboxclient.SSHTarget{Host: target.SSHHost, Port: target.SSHPort, SandboxID: target.Detail.Id, Credential: toolClient.Credential()},
				toolboxclient.SSHCommand{Options: sshArgs, Args: remoteCommand},
			)
			if err != nil {
				return err
			}
			defer invocation.Cleanup()
			// OpenSSH prints the gateway username, which carries the
			// credential, in its own messages (an authentication failure,
			// -v output): its stderr is passed through with the credential
			// replaced.
			stderr := newSecretRedactor(os.Stderr, toolClient.Credential())
			runErr := runSSHCommand(cmd.Context(), sshBinary, invocation.Args, stderr)
			_ = stderr.Flush()
			return sshExitError(cmd.Context(), runErr)
		},
	}
	cmd.Flags().SetInterspersed(false)
	return cmd
}

// sshExitError passes the ssh client's exit status through. When the CLI's
// own context ended (Ctrl-C, SIGTERM, which also kills ssh), the CLI's
// interruption code wins; an ssh killed by a signal exits 128 + its number.
func sshExitError(ctx context.Context, err error) error {
	if err == nil {
		return nil
	}
	if ctxErr := cerr.FromContext(ctx); ctxErr != nil {
		return ctxErr
	}
	var exitErr *exec.ExitError
	if !errors.As(err, &exitErr) {
		return err
	}
	code := exitErr.ExitCode()
	if code < 0 {
		code = cerr.ExitFailure
		if status, ok := exitErr.Sys().(interface{ Signal() syscall.Signal }); ok && status.Signal() > 0 {
			code = 128 + int(status.Signal())
		}
		return cerr.New(code, "ssh terminated by a signal").WithSource(cerr.SourceSSH)
	}
	return cerr.New(code, "ssh exited with code %d", code).WithSource(cerr.SourceSSH)
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

var runSSHCommand = func(ctx context.Context, binary string, args []string, stderr io.Writer) error {
	cmd := exec.CommandContext(ctx, binary, args...)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = stderr
	// A process ssh leaves behind (`-f`, a ControlPersist master) may hold the
	// stderr pipe open: stop copying from it a second after ssh exits. That
	// is not a failure of ssh, whose own exit status still decides.
	cmd.WaitDelay = time.Second
	err := cmd.Run()
	if errors.Is(err, exec.ErrWaitDelay) {
		return nil
	}
	return err
}

// secretRedacted replaces a secret in redacted output.
const secretRedacted = "<redacted>"

// secretRedactorHold is how long bytes that may start the secret are held
// back when nothing follows them, so a prompt is not kept from the user.
var secretRedactorHold = 50 * time.Millisecond

// secretRedactor writes a stream through with every occurrence of a secret
// replaced. A write that ends with the start of the secret holds those bytes
// back until the next write shows whether the secret follows, so a secret
// split across writes is replaced too; held bytes are written once
// secretRedactorHold passes with no further write, or on Flush.
type secretRedactor struct {
	mu      sync.Mutex
	w       io.Writer
	secret  []byte
	pending []byte
	timer   *time.Timer
	// generation counts writes, so a flush timer armed before a later write
	// never releases bytes that write held back.
	generation uint64
}

func newSecretRedactor(w io.Writer, secret string) *secretRedactor {
	return &secretRedactor{w: w, secret: []byte(secret)}
}

func (r *secretRedactor) Write(p []byte) (int, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if len(r.secret) == 0 {
		return r.w.Write(p)
	}
	data := bytes.ReplaceAll(append(r.pending, p...), r.secret, []byte(secretRedacted))
	held := 0
	for n := min(len(r.secret)-1, len(data)); n > 0; n-- {
		if bytes.HasSuffix(data, r.secret[:n]) {
			held = n
			break
		}
	}
	r.pending = append([]byte(nil), data[len(data)-held:]...)
	r.generation++
	if r.timer != nil {
		r.timer.Stop()
		r.timer = nil
	}
	if held > 0 {
		generation := r.generation
		r.timer = time.AfterFunc(secretRedactorHold, func() { _ = r.flushIf(generation) })
	}
	if _, err := r.w.Write(data[:len(data)-held]); err != nil {
		return 0, err
	}
	return len(p), nil
}

// flushIf writes the bytes held back by the write of that generation, if no
// write came since.
func (r *secretRedactor) flushIf(generation uint64) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.generation != generation {
		return nil
	}
	return r.flushLocked()
}

// Flush writes the bytes held back: no part of the secret followed them.
func (r *secretRedactor) Flush() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.flushLocked()
}

func (r *secretRedactor) flushLocked() error {
	if r.timer != nil {
		r.timer.Stop()
		r.timer = nil
	}
	if len(r.pending) == 0 {
		return nil
	}
	_, err := r.w.Write(r.pending)
	r.pending = nil
	return err
}
