// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build unix

package common

import (
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"syscall"
)

// ExecuteSSH connects to the target with proper terminal handling. The
// access token reaches ssh through a temporary ssh_config, never through the
// command line (see NewSSHInvocation).
func ExecuteSSH(target SSHTarget) error {
	sshPath, err := exec.LookPath("ssh")
	if err != nil {
		return fmt.Errorf("ssh not found in PATH: %w", err)
	}

	// Handle signals - forward them to the SSH process. They are caught from
	// before the config holding the token is written, and SIGHUP (terminal
	// closed) and SIGQUIT are caught too, so the CLI outlives ssh and removes
	// the config.
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM, syscall.SIGWINCH, syscall.SIGHUP, syscall.SIGQUIT)

	invocation, err := NewSSHInvocation(target)
	if err != nil {
		signal.Stop(sigChan)
		return err
	}
	defer invocation.Cleanup()

	// Create the command
	sshCmd := exec.Command(sshPath, invocation.Args...)
	sshCmd.Stdin = os.Stdin
	sshCmd.Stdout = os.Stdout
	sshCmd.Stderr = os.Stderr

	// Start the SSH process
	if err := sshCmd.Start(); err != nil {
		signal.Stop(sigChan)
		return fmt.Errorf("failed to start SSH: %w", err)
	}

	// Forward signals to the SSH process
	go func() {
		for sig := range sigChan {
			if sshCmd.Process != nil {
				_ = sshCmd.Process.Signal(sig)
			}
		}
	}()

	// Wait for SSH to complete
	err = sshCmd.Wait()

	// Remove the config holding the token while signals are still caught,
	// and before os.Exit below, which skips deferred calls.
	invocation.Cleanup()

	// Stop signal handling
	signal.Stop(sigChan)
	close(sigChan)

	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			os.Exit(exitErr.ExitCode())
		}
		return err
	}

	return nil
}
