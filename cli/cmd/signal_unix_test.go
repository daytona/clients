// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build unix

package cmd

import (
	"bufio"
	"errors"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"testing"
	"time"
)

const signalHelperEnv = "DAYTONA_TEST_SIGNAL_HELPER"

// TestSignalHelperProcess is the child of TestSecondInterruptTerminates: it
// waits for the first SIGINT, then hangs like a stuck command.
func TestSignalHelperProcess(t *testing.T) {
	if os.Getenv(signalHelperEnv) != "1" {
		t.Skip("helper process")
	}
	ctx, cancel := signalContext()
	defer cancel()
	_, _ = os.Stdout.WriteString("ready\n")
	<-ctx.Done()
	_, _ = os.Stdout.WriteString("cancelled\n")
	select {}
}

func TestSecondInterruptTerminates(t *testing.T) {
	child := exec.Command(os.Args[0], "-test.run=^TestSignalHelperProcess$")
	child.Env = append(os.Environ(), signalHelperEnv+"=1")
	stdout, err := child.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = child.Process.Kill() }()
	lines := bufio.NewScanner(stdout)
	waitFor := func(want string) {
		t.Helper()
		for lines.Scan() {
			if strings.TrimSpace(lines.Text()) == want {
				return
			}
		}
		t.Fatalf("helper ended before printing %q", want)
	}
	waitFor("ready")
	if err := child.Process.Signal(syscall.SIGINT); err != nil {
		t.Fatal(err)
	}
	waitFor("cancelled")

	exited := make(chan error, 1)
	go func() { exited <- child.Wait() }()
	deadline := time.After(5 * time.Second)
	tick := time.NewTicker(50 * time.Millisecond)
	defer tick.Stop()
	for {
		select {
		case err := <-exited:
			var exitErr *exec.ExitError
			if !errors.As(err, &exitErr) {
				t.Fatalf("helper exit: %v", err)
			}
			status, ok := exitErr.Sys().(syscall.WaitStatus)
			if !ok || !status.Signaled() || status.Signal() != syscall.SIGINT {
				t.Fatalf("want termination by SIGINT, got %v", err)
			}
			return
		case <-tick.C:
			_ = child.Process.Signal(syscall.SIGINT)
		case <-deadline:
			t.Fatal("a second Ctrl+C after cancellation did not terminate the process")
		}
	}
}
