// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package ui_test

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/ui"
	"golang.org/x/term"
)

func TestPromptCancellationRestoresTerminal(t *testing.T) {
	for _, prompt := range []string{"confirm", "select"} {
		t.Run(prompt, func(t *testing.T) {
			// Given a real terminal and a signal context passed to the prompt.
			binary, err := os.Executable()
			if err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithTimeout(t.Context(), 3*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, binary, "-test.run=^TestPromptCancellationProcess$")
			cmd.Env = []string{"TERM=xterm-256color", "CLI_TEST_CANCEL_PROMPT=" + prompt}
			// When SIGTERM cancels the context without any keyboard input.
			out, err := runTerminal(t, cmd, terminalInput{signal: syscall.SIGTERM})
			// Then the blocked read ends and cleanup restores terminal modes.
			if err != nil {
				t.Fatalf("prompt did not finish: %v (%v): %q", err, ctx.Err(), out)
			}
			if !strings.Contains(out, "PROMPT_CANCEL_EXIT=130\r\n") {
				t.Fatalf("unexpected cancellation result: %q", out)
			}
			if !strings.Contains(out, "\x1b[?2004l") {
				t.Fatalf("bracketed paste mode was not restored: %q", out)
			}
			if strings.Contains(out, "\x1b]11;") || strings.Contains(out, "\x1b[6n") {
				t.Fatalf("terminal query during cancellation: %q", out)
			}
		})
	}
}

func TestPromptCancellationProcess(t *testing.T) {
	prompt := os.Getenv("CLI_TEST_CANCEL_PROMPT")
	if prompt == "" {
		return
	}
	ctx, cancel := signal.NotifyContext(t.Context(), syscall.SIGTERM)
	defer cancel()
	before, err := term.GetState(int(os.Stdin.Fd()))
	if err != nil {
		t.Fatal(err)
	}
	switch prompt {
	case "confirm":
		value, promptErr := ui.ConfirmContext(ctx, "Confirm")
		if value {
			t.Fatal("cancelled confirmation returned true")
		}
		err = promptErr
	case "select":
		value, promptErr := ui.SelectContext(ctx, "Select", []ui.Option{{Label: "first", Value: "first"}})
		if value != "" {
			t.Fatalf("cancelled selection returned %q", value)
		}
		err = promptErr
	}
	if cerr.ExitCode(err) != cerr.ExitInterrupted || !errors.Is(err, context.Canceled) {
		t.Fatalf("unexpected prompt error: %v (exit %d)", err, cerr.ExitCode(err))
	}
	after, err := term.GetState(int(os.Stdin.Fd()))
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(before, after) {
		t.Fatal("cancellation did not restore terminal state")
	}
	fmt.Printf("PROMPT_CANCEL_EXIT=%d\n", cerr.ExitInterrupted)
}
