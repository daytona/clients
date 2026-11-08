// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/workload"
)

// annotationCreatesProfile marks the command (login) whose --profile may
// name a profile that does not exist yet.
const annotationCreatesProfile = "daytona/creates-profile"

// defaultWaitTimeout is the --timeout default of commands that wait.
const defaultWaitTimeout = "10m"

// addFileFlag registers --file/-f on a command that takes a JSON request
// body. Only these commands accept it.
func addFileFlag(cmd *cobra.Command, help string) {
	if help == "" {
		help = "JSON request body from a file (or - for stdin); explicit flags override its fields"
	}
	cmd.Flags().StringP("file", "f", "", help)
	flagPlaceholder(cmd.Flags(), "file", "PATH")
}

// addWaitFlags registers --no-wait and --timeout on a command that waits for
// an operation to finish.
func addWaitFlags(cmd *cobra.Command) {
	cmd.Flags().Bool("no-wait", false, "return once the server accepts the operation instead of waiting for it to finish")
	addTimeoutFlag(cmd, defaultWaitTimeout, "how long to wait for the operation to finish (Go duration); the server keeps working after it")
}

// addTimeoutFlag registers --timeout DURATION, the command's local wait. The
// value is checked while flags are parsed: a Go duration greater than zero.
func addTimeoutFlag(cmd *cobra.Command, def, help string) {
	value := waitTimeoutFlag(def)
	cmd.Flags().Var(&value, "timeout", help)
	flagPlaceholder(cmd.Flags(), "timeout", "DURATION")
}

// waitTimeoutFlag is the value of --timeout.
type waitTimeoutFlag string

func (f *waitTimeoutFlag) String() string { return string(*f) }

func (f *waitTimeoutFlag) Type() string { return "duration" }

func (f *waitTimeoutFlag) Set(raw string) error {
	if _, err := workload.ParseTimeout(raw); err != nil {
		return fmt.Errorf("want a Go duration greater than zero, such as 30s or 10m")
	}
	*f = waitTimeoutFlag(raw)
	return nil
}

// commandGlobals completes the root's globals with what this command's
// parsed flags say: the per-command --file / --no-wait / --timeout, and
// whether --api-key was given at all.
func commandGlobals(cmd *cobra.Command, g app.Globals) app.Globals {
	flags := cmd.Flags()
	if f := flags.Lookup("file"); f != nil {
		g.File = f.Value.String()
	}
	if f := flags.Lookup("no-wait"); f != nil {
		g.NoWait = f.Value.String() == "true"
	}
	if f := flags.Lookup("timeout"); f != nil {
		g.Timeout = f.Value.String()
	}
	g.APIKeySet = flags.Changed("api-key")
	g.ProfileMayBeNew = cmd.Annotations[annotationCreatesProfile] != ""
	return g
}
