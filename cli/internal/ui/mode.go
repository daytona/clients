// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

// Package ui decides how the CLI presents itself: interactively (a human at a
// terminal) or strictly (a script, a CI job, or an agent reading stdout).
//
// The rule is the industry one:
//
//	interactive := isatty(stdout) && isatty(stdin) && output != json && !CI
//
// Interactive mode may render colour, spinners, pickers and confirmation
// prompts. Non-interactive mode never prompts: a missing argument or a
// destructive verb without --yes is a usage error that names the flag.
package ui

import (
	"os"
	"strings"

	"golang.org/x/term"
)

// Mode is the resolved presentation mode for one invocation.
type Mode struct {
	// Interactive reports whether pickers, spinners and confirmation prompts
	// may be shown.
	Interactive bool
	// Color reports whether ANSI colour may be emitted on stdout/stderr.
	Color bool
	// AssumeYes reports whether destructive verbs skip their confirmation:
	// --yes or DAYTONA_YES=1 always; config confirm=false only when the
	// invocation is interactive (it turns prompts off, it never consents for
	// a script or an agent).
	AssumeYes bool
}

// Options are the inputs Resolve reads besides the environment.
type Options struct {
	// OutputFormat is the value of --output (table|json|yaml).
	OutputFormat string
	// YesFlag is --yes.
	YesFlag bool
	// ConfirmOff is the persisted `confirm: false` config value. The zero
	// value keeps confirmation prompts on. It applies to interactive
	// invocations only.
	ConfirmOff bool
	// Getenv is os.Getenv unless a test injects one.
	Getenv func(string) string
	// StdoutTTY/StdinTTY override the isatty probes (tests).
	StdoutTTY, StdinTTY *bool
}

// Resolve computes the Mode for this process.
func Resolve(o Options) Mode {
	getenv := o.Getenv
	if getenv == nil {
		getenv = os.Getenv
	}
	outTTY := isTTY(os.Stdout.Fd())
	if o.StdoutTTY != nil {
		outTTY = *o.StdoutTTY
	}
	inTTY := isTTY(os.Stdin.Fd())
	if o.StdinTTY != nil {
		inTTY = *o.StdinTTY
	}
	ci := getenv("CI") != ""
	jsonOut := strings.EqualFold(o.OutputFormat, "json") || strings.EqualFold(o.OutputFormat, "yaml")
	interactive := outTTY && inTTY && !jsonOut && !ci

	color := interactive && getenv("NO_COLOR") == "" && getenv("TERM") != "dumb"

	yes := o.YesFlag || (o.ConfirmOff && interactive)
	if v := getenv("DAYTONA_YES"); v == "1" || strings.EqualFold(v, "true") {
		yes = true
	}
	return Mode{Interactive: interactive, Color: color, AssumeYes: yes}
}

func isTTY(fd uintptr) bool { return term.IsTerminal(int(fd)) }
