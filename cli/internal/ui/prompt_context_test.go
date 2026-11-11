// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package ui_test

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/ui"
)

func TestPromptRefusesEndedContextBeforeOpeningTerminal(t *testing.T) {
	for name, prompt := range map[string]func(context.Context) error{
		"confirm": func(ctx context.Context) error {
			_, err := ui.ConfirmContext(ctx, "Confirm")
			return err
		},
		"select": func(ctx context.Context) error {
			_, err := ui.SelectContext(ctx, "Select", []ui.Option{{Label: "first", Value: "first"}})
			return err
		},
	} {
		t.Run(name+"/cancelled", func(t *testing.T) {
			// Given an ended context carrying a caller-owned cancellation cause.
			ctx, cancel := context.WithCancelCause(t.Context())
			cause := errors.New("test cancellation cause")
			cancel(cause)
			// When opening a context-aware prompt without a terminal.
			err := prompt(ctx)
			// Then it returns the CLI interruption and preserves the context cause.
			if cerr.ExitCode(err) != cerr.ExitInterrupted || !errors.Is(err, context.Canceled) {
				t.Fatalf("got %v (exit %d)", err, cerr.ExitCode(err))
			}
			if !errors.Is(context.Cause(ctx), cause) {
				t.Fatal("prompt replaced the caller's cancellation cause")
			}
		})
		t.Run(name+"/deadline", func(t *testing.T) {
			// Given a context whose deadline is already past.
			ctx, cancel := context.WithDeadline(t.Context(), time.Time{})
			defer cancel()
			// When opening a context-aware prompt without a terminal.
			err := prompt(ctx)
			// Then the caller's deadline retains the CLI timeout classification.
			if cerr.ExitCode(err) != cerr.ExitTimeout || !errors.Is(err, context.DeadlineExceeded) {
				t.Fatalf("got %v (exit %d)", err, cerr.ExitCode(err))
			}
		})
	}
}
