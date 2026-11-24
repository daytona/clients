// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"io"
	"regexp"
	"sort"
	"strings"
	"testing"

	"github.com/spf13/cobra"
	"github.com/stretchr/testify/require"

	"github.com/daytona/clients/cli/internal/cerr"
)

// leafCommands returns every runnable leaf of the command tree but help.
func leafCommands() []*cobra.Command {
	var out []*cobra.Command
	var walk func(c *cobra.Command)
	walk = func(c *cobra.Command) {
		for _, sub := range c.Commands() {
			walk(sub)
		}
		if c.Runnable() && !c.HasSubCommands() && c.Name() != "help" {
			out = append(out, c)
		}
	}
	walk(NewRoot(io.Discard, io.Discard))
	return out
}

// Every named positional of every command refuses an empty or whitespace-only
// value as a usage error before any credential, picker or request: sent as
// given, `sandbox get ""` asked for the sandbox collection and
// `create-from-snapshot ""` for a snapshot named "".
func TestBlankPositionalArgumentsFailLocally(t *testing.T) {
	type invocation struct {
		path, name string
		argv       []string
	}
	var cases []invocation
	for _, c := range leafCommands() {
		path := strings.Fields(c.CommandPath())[1:]
		count := len(usePositionals(c.Use))
		for i, placeholder := range namedPositionals(c) {
			for _, blank := range []string{"", "  "} {
				args := make([]string, count)
				for j := range args {
					args[j] = "x"
				}
				args[i] = blank
				if c.Name() == "exec" {
					args = append(args, "--", "true")
				}
				cases = append(cases, invocation{path: strings.Join(path, " "), name: placeholder, argv: append(append([]string{}, path...), args...)})
			}
		}
	}
	require.NotEmpty(t, cases)

	covered := map[string]bool{}
	for _, tc := range cases {
		f := newFakeAPI(t)
		_, stderr, code := run(t, cfgDir(t), map[string]string{"DAYTONA_V2_API_URL": f.srv.URL, "DAYTONA_API_KEY": "k"}, tc.argv...)
		label := strings.Join(tc.argv, " ") + " (" + tc.name + ")"
		require.Equal(t, cerr.ExitUsage, code, "%s: %s", label, stderr)
		require.Contains(t, stderr, "argument "+tc.name+" is empty", label)
		require.Empty(t, f.requests, label)
		covered[tc.path] = true
	}
	for _, path := range []string{
		"sandbox get", "sandbox delete", "sandbox start", "sandbox update", "sandbox exec",
		"sandbox create-from-snapshot", "reservation get", "registry get", "project use", "org get",
	} {
		require.True(t, covered[path], path)
	}
}

// The placeholder vocabulary is pinned, independently of the parser the
// check uses: a command whose usage line introduces a new placeholder fails
// here until it is added below (blank refused) or its command is marked free
// text with annotationFreeTextFrom (blank kept, as for a search query).
func TestPositionalPlaceholdersAreKnown(t *testing.T) {
	refused := map[string]bool{
		"<id>": true, "ID": true, "<id|name>": true, "<id-or-name>": true, "<name>": true,
		"<host>": true, "<sandbox>": true, "<sandbox-id>": true, "<process-id>": true, "<signal>": true,
		"<port>": true, "<src>": true, "<dst>": true, "<user>": true, "<email>": true,
		"<token|link>": true, "<code>": true, "<key>": true, "<image>": true, "<service-account>": true,
		"<snapshot-name-or-snap_id>": true, "<starter|team|enterprise>": true, "<amount-cents>": true,
	}
	freeText := map[string]string{"daytona sandbox search": "<query>", "daytona config set": "<value>"}
	placeholder := regexp.MustCompile(`^(<[^<>]+>|\[[^\[\]]+\]|[A-Z][A-Z_]*)$`)
	var unknown, annotated []string
	for _, c := range leafCommands() {
		fields := strings.Fields(c.Use)[1:]
		for _, field := range fields {
			if field == "[flags]" {
				continue
			}
			if strings.HasPrefix(field, "-") || strings.Contains(field, "...") {
				break
			}
			require.Regexp(t, placeholder, field, c.CommandPath())
		}
		names := usePositionals(c.Use)
		checked := namedPositionals(c)
		for _, name := range names[len(checked):] {
			annotated = append(annotated, c.CommandPath()+" "+name)
			require.Equal(t, freeText[c.CommandPath()], name, c.CommandPath())
		}
		for _, name := range checked {
			if !refused[name] {
				unknown = append(unknown, c.CommandPath()+" "+name)
			}
		}
	}
	sort.Strings(annotated)
	require.Equal(t, []string{"daytona config set <value>", "daytona sandbox search <query>"}, annotated)
	require.Empty(t, unknown, "new placeholders: add them to refused, or mark the command with annotationFreeTextFrom")
}

// Free-text and pass-through positionals stay as given: an empty search
// query, a setting's value (its command names the allowed values), and the
// arguments of the command `sandbox exec` runs.
func TestUsePositionals(t *testing.T) {
	for use, want := range map[string][]string{
		"get <id>":                      {"<id>"},
		"get ID":                        {"ID"},
		"use [id|name]":                 {"<id|name>"},
		"update <id|name> --name <new>": {"<id|name>"},
		"exec <sandbox-id> -- <command> [args...]":         {"<sandbox-id>"},
		"ssh [flags] <sandbox-id> [ssh-args...] [command]": {"<sandbox-id>"},
		"search [query]":    {"<query>"},
		"set <key> <value>": {"<key>", "<value>"},
		"list":              nil,
	} {
		require.Equal(t, want, usePositionals(use), use)
	}

	f := newFakeAPI(t)
	env := map[string]string{"DAYTONA_V2_API_URL": f.srv.URL, "DAYTONA_API_KEY": "k"}
	_, stderr, code := run(t, cfgDir(t), env, "config", "set", "confirm", "")
	require.Equal(t, cerr.ExitUsage, code, stderr)
	require.Contains(t, stderr, "true|false")
	require.Empty(t, f.requests)

	f.on("GET", "/sandboxes/search", 200, `{"total":0,"total_relation":"eq","sandboxes":[]}`)
	_, stderr, code = run(t, cfgDir(t), env, "sandbox", "search", "")
	require.Equal(t, 0, code, stderr)
	findRequest(t, f, "GET", "/sandboxes/search")
}
