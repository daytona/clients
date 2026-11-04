// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"io"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestUnknownCommands_cannotBeDispatched(t *testing.T) {
	// Given the registered public command tree.
	root := NewRoot(io.Discard, io.Discard)
	for _, args := range [][]string{
		{"unknown-command"},
		{"sandbox", "unknown-command"},
	} {
		t.Run(args[len(args)-1], func(t *testing.T) {
			// When dispatch searches for an unknown command.
			found, rest, err := root.Find(args)
			// Then no command consumes its name (Cobra may return the parent help).
			if err == nil {
				require.NotEqual(t, args[len(args)-1], found.Name())
				require.Contains(t, rest, args[len(args)-1])
			}
		})
	}
}

func TestUnknownCreateFlags_areRejected(t *testing.T) {
	for _, surface := range []string{"create", "create-from-snapshot", "create-macos"} {
		for _, flag := range []string{"--unknown-flag=value"} {
			t.Run(surface+flag, func(t *testing.T) {
				// Given an unknown flag on an otherwise valid create command.
				args := []string{"sandbox", surface}
				if surface == "create-from-snapshot" {
					args = append(args, "saved")
				}
				// When flags are parsed.
				_, _, err := runCLI(t, append(args, flag), nil)
				// Then parsing fails without any API request.
				require.Error(t, err)
				require.ErrorContains(t, err, "unknown flag")
			})
		}
	}
}

func TestUnknownSearchFlag_isRejected(t *testing.T) {
	// Given an unknown filter, when the CLI parses the search.
	_, _, err := runCLI(t, []string{"sandbox", "search", "--unknown-flag"}, nil)
	// Then it rejects the flag without an API request.
	require.Error(t, err)
	require.ErrorContains(t, err, "unknown flag")
}
