// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"io"
	"strings"
	"testing"

	"github.com/spf13/cobra"
	"github.com/stretchr/testify/require"

	"github.com/daytona/clients/cli/internal/cerr"
)

// Every named positional of every command refuses an empty or whitespace-only
// value as a usage error before any credential, picker or request: sent as
// given, `sandbox get ”` asked for the sandbox collection and
// `create-from-snapshot ”` for a snapshot named "".
func TestBlankPositionalArgumentsFailLocally(t *testing.T) {
	type invocation struct {
		path []string
		args []string
		name string
	}
	var cases []invocation
	var walk func(c *cobra.Command)
	walk = func(c *cobra.Command) {
		for _, sub := range c.Commands() {
			walk(sub)
		}
		if !c.Runnable() || c.HasSubCommands() || c.Name() == "help" {
			return
		}
		placeholders := namedPositionals(c.Use)
		path := strings.Fields(c.CommandPath())[1:]
		for i, placeholder := range placeholders {
			for _, blank := range []string{"", "  "} {
				args := make([]string, positionalCount(c.Use))
				for j := range args {
					args[j] = "x"
				}
				args[i] = blank
				if c.Name() == "exec" {
					args = append(args, "--", "true")
				}
				cases = append(cases, invocation{path: path, args: args, name: placeholder})
			}
		}
	}
	walk(NewRoot(io.Discard, io.Discard))
	require.NotEmpty(t, cases)

	covered := map[string]bool{}
	for _, tc := range cases {
		f := newFakeAPI(t)
		argv := append(append([]string{}, tc.path...), tc.args...)
		_, stderr, code := run(t, cfgDir(t), map[string]string{"DAYTONA_V2_API_URL": f.srv.URL, "DAYTONA_API_KEY": "k"}, argv...)
		label := strings.Join(argv, " ") + " (" + tc.name + ")"
		require.Equal(t, cerr.ExitUsage, code, "%s: %s", label, stderr)
		require.Contains(t, stderr, "argument "+tc.name+" is empty", label)
		require.Empty(t, f.requests, label)
		covered[strings.Join(tc.path, " ")] = true
	}
	for _, path := range []string{
		"sandbox get", "sandbox delete", "sandbox start", "sandbox update", "sandbox exec",
		"sandbox create-from-snapshot", "reservation get", "registry get", "project use", "org get",
	} {
		require.True(t, covered[path], path)
	}
}

// positionalCount counts every positional placeholder of a Use line before the
// first flag or variadic one, the free-text ones included.
func positionalCount(use string) int {
	n := 0
	for _, field := range strings.Fields(use)[1:] {
		if strings.HasPrefix(field, "-") || strings.Contains(field, "...") {
			break
		}
		if field != "[flags]" {
			n++
		}
	}
	return n
}

// Free-text and pass-through positionals stay as given: an empty search
// query, a setting's value (its command names the allowed values), and the
// arguments of the command `sandbox exec` runs.
func TestNamedPositionals(t *testing.T) {
	for use, want := range map[string][]string{
		"get <id>":                      {"<id>"},
		"get ID":                        {"ID"},
		"use [id|name]":                 {"<id|name>"},
		"update <id|name> --name <new>": {"<id|name>"},
		"exec <sandbox-id> -- <command> [args...]":         {"<sandbox-id>"},
		"ssh [flags] <sandbox-id> [ssh-args...] [command]": {"<sandbox-id>"},
		"search [query]":    nil,
		"set <key> <value>": {"<key>"},
		"list":              nil,
	} {
		require.Equal(t, want, namedPositionals(use), use)
	}

	f := newFakeAPI(t)
	_, stderr, code := run(t, cfgDir(t), nil, "config", "set", "confirm", "")
	require.Equal(t, cerr.ExitUsage, code, stderr)
	require.Contains(t, stderr, "true|false")
	require.Empty(t, f.requests)
}
