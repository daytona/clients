// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package app

import (
	"context"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/ui"
	"golang.org/x/term"
)

// Printer returns the stdout printer.
func (a *App) Printer() output.Printer {
	return output.Printer{W: a.Stdout, Format: a.Format, Color: a.Mode.Color}
}

// Info prints a diagnostic line to stderr unless --quiet.
func (a *App) Info(format string, args ...any) {
	if a.Globals.Quiet {
		return
	}
	// Arguments are often server-controlled (names, messages): strip terminal
	// control sequences, and keep each argument on this one line so it cannot
	// forge further diagnostic lines.
	clean := make([]any, len(args))
	for i, arg := range args {
		switch v := arg.(type) {
		case string:
			clean[i] = output.SanitizeLine(v)
		case error:
			clean[i] = output.SanitizeLine(v.Error())
		default:
			clean[i] = arg
		}
	}
	fmt.Fprintln(a.Stderr, output.SanitizeLine(fmt.Sprintf(format, clean...)))
}

// PreflightConfirm is the network-free half of Confirm: in
// non-interactive mode a destructive verb without --yes must fail with exit 2
// BEFORE any request is made, so an agent gets the promised refusal rather
// than a network error or a resolved-then-refused round trip. Call it first
// in RunE with the raw argument; Confirm (with the resolved, human-readable
// name) still runs later for the interactive prompt.
func (a *App) PreflightConfirm(verb, target string) error {
	if a.Mode.AssumeYes || a.Mode.Interactive {
		return nil
	}
	return cerr.RefuseWithoutYes(output.SanitizeLine(verb), output.SanitizeLine(target))
}

// Confirm prompts on a TTY and requires --yes otherwise.
func (a *App) Confirm(verb, target string) error {
	if a.Mode.AssumeYes {
		return nil
	}
	if !a.Mode.Interactive {
		return cerr.RefuseWithoutYes(verb, target)
	}
	ctx := a.Ctx
	if ctx == nil {
		ctx = context.Background()
	}
	ok, err := ui.ConfirmContext(ctx, fmt.Sprintf("%s %s?", capitalize(verb), target))
	if err != nil && ctx.Err() != nil {
		return cerr.FromContext(ctx)
	}
	if err != nil {
		return err
	}
	if !ok {
		return cerr.New(cerr.ExitInterrupted, "aborted")
	}
	return nil
}

// ResolveFormat parses --output; an empty value selects the default: table
// when stdout is a terminal, JSON otherwise.
func ResolveFormat(flag string, stdoutTTY bool) (output.Format, error) {
	if strings.TrimSpace(flag) == "" {
		if stdoutTTY {
			return output.FormatTable, nil
		}
		return output.FormatJSON, nil
	}
	f, err := output.ParseFormat(flag)
	if err != nil {
		return "", cerr.Usage("%v", err)
	}
	return f, nil
}

// WriterIsTerminal reports whether w is a terminal (ForceTTY overrides the
// probe in tests). Anything that is not an *os.File is not a terminal.
func WriterIsTerminal(w io.Writer) bool {
	if ForceTTY != nil {
		return *ForceTTY
	}
	f, ok := w.(*os.File)
	return ok && term.IsTerminal(int(f.Fd()))
}

func capitalize(s string) string {
	if s == "" {
		return s
	}
	return strings.ToUpper(s[:1]) + s[1:]
}
