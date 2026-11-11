//go:build linux

// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package ui_test

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/ui"
)

func TestPickerCtrlCReportsInterrupted(t *testing.T) {
	// Given a picker running in a real terminal.
	binary, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(t.Context(), 3*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, binary, "-test.run=^TestPickerCtrlCReviewProcess$")
	cmd.Env = []string{"TERM=xterm-256color", "CLI_TEST_REVIEW_PICKER=1"}
	// When Ctrl-C is delivered as raw terminal input.
	out, err := runTerminal(t, cmd, terminalInput{prompt: "\x03"})
	// Then the child verifies interruption classification and sentinel identity.
	if err != nil {
		t.Fatalf("picker failed: %v: %q", err, out)
	}
}

func TestPickerCtrlCReviewProcess(t *testing.T) {
	if os.Getenv("CLI_TEST_REVIEW_PICKER") == "" {
		return
	}
	// Given a real picker with one selectable entry.
	options := []ui.Option{{Label: "first", Value: "first"}}
	// When the parent sends Ctrl-C.
	value, err := ui.SelectContext(t.Context(), "Select", options)
	// Then cancellation retains errors.Is compatibility and exits 130.
	if value != "" || cerr.ExitCode(err) != cerr.ExitInterrupted || !errors.Is(err, ui.ErrPromptAborted) {
		t.Fatalf("got %q, %v (exit %d)", value, err, cerr.ExitCode(err))
	}
}
