// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/cobra"
	"github.com/stretchr/testify/require"

	"github.com/daytona/clients/cli/internal/config"
)

// complete runs `daytona __complete args...` and returns the candidates and
// the directive line.
func complete(t *testing.T, dir string, args ...string) ([]string, string) {
	t.Helper()
	stdout, _, code := run(t, dir, nil, append([]string{cobra.ShellCompNoDescRequestCmd}, args...)...)
	require.Equal(t, 0, code)
	lines := strings.Split(strings.TrimRight(stdout, "\n"), "\n")
	require.NotEmpty(t, lines)
	directive := lines[len(lines)-1]
	var candidates []string
	for _, line := range lines[:len(lines)-1] {
		if line != "" {
			candidates = append(candidates, line)
		}
	}
	return candidates, directive
}

const noFileComp = ":4"

func TestCompletionOffersFlagValues(t *testing.T) {
	dir := cfgDir(t)
	for _, tc := range []struct {
		args []string
		want []string
	}{
		{[]string{"sandbox", "list", "-o", ""}, []string{"table", "json", "yaml"}},
		{[]string{"sandbox", "list", "--output", "j"}, []string{"json"}},
		{[]string{"sandbox", "wait", "sbx", "--for", ""}, []string{"running", "stopped", "paused", "deleted", "started", "destroyed"}},
		{[]string{"volume", "create", "v", "--type", ""}, []string{"disk", "bucket"}},
		{[]string{"image", "list", "--platform", ""}, []string{"true", "false", "any"}},
		{[]string{"sandbox", "process", "list", "sbx", "--kind", ""}, []string{"exec", "pty", "code", "session"}},
		{[]string{"registry", "set", "r.example", "--kind", "ecr"}, []string{"ecr", "ecr_role"}},
		{[]string{"sandbox", "create-macos", "--on-idle-timeout", ""}, []string{"delete", "stop"}},
		{[]string{"sandbox", "create", "--on-idle-timeout", ""}, []string{"delete", "stop", "pause"}},
		{[]string{"sandbox", "search", "--status", "pa"}, []string{"paused", "pausing"}},
	} {
		got, directive := complete(t, dir, tc.args...)
		require.ElementsMatch(t, tc.want, got, strings.Join(tc.args, " "))
		require.Equal(t, noFileComp, directive, strings.Join(tc.args, " "))
	}
}

func TestCompletionOffersArgumentValues(t *testing.T) {
	dir := cfgDir(t)
	got, directive := complete(t, dir, "config", "set", "")
	require.Equal(t, []string{"confirm"}, got)
	require.Equal(t, noFileComp, directive)
	got, _ = complete(t, dir, "config", "set", "confirm", "")
	require.Equal(t, []string{"true", "false"}, got)
	got, _ = complete(t, dir, "sandbox", "process", "signal", "sbx", "proc", "")
	require.Contains(t, got, "TERM")
	got, _ = complete(t, dir, "billing", "plan", "set", "t")
	require.Equal(t, []string{"team"}, got)
}

func TestCompletionOffersSavedProfileNames(t *testing.T) {
	dir := cfgDir(t)
	store, err := config.Open(dir)
	require.NoError(t, err)
	require.NoError(t, store.SaveConfig(&config.Config{Profiles: []config.Profile{{Name: "work", APIURL: "https://api.example"}, {Name: "staging", APIURL: "https://api.example"}}}))

	got, directive := complete(t, dir, "profile", "use", "")
	require.ElementsMatch(t, []string{"work", "staging"}, got)
	require.Equal(t, noFileComp, directive)
	got, _ = complete(t, dir, "sandbox", "list", "--profile", "w")
	require.Equal(t, []string{"work"}, got)
	got, _ = complete(t, dir, "profile", "delete", "work", "")
	require.Empty(t, got)
}

func TestCompletionOfProfilesCreatesNothing(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "absent")
	got, directive := complete(t, dir, "profile", "use", "")
	require.Empty(t, got)
	require.Equal(t, noFileComp, directive)
	// run() itself may create the directory for the __complete command;
	// the completion function must not write a config file.
	_, err := os.Stat(filepath.Join(dir, "config.yaml"))
	require.ErrorIs(t, err, os.ErrNotExist)
}

// Resource positionals never fall back to local file names; the local
// endpoint of `sandbox cp` still does.
func TestCompletionDisablesFileNamesForResourceArguments(t *testing.T) {
	dir := cfgDir(t)
	for _, args := range [][]string{
		{"sandbox", "get", ""},
		{"sandbox", "delete", ""},
		{"image", "get", ""},
		{"volume", "delete", ""},
		{"sandbox", "exec", ""},
	} {
		got, directive := complete(t, dir, args...)
		require.Empty(t, got, strings.Join(args, " "))
		require.Equal(t, noFileComp, directive, strings.Join(args, " "))
	}
	_, directive := complete(t, dir, "sandbox", "cp", "")
	require.Equal(t, ":0", directive)
}

// Every completion entry names a command and a flag that exist, so a renamed
// flag or command cannot leave a dead entry behind.
func TestCompletionTableNamesExistingFlags(t *testing.T) {
	root := NewRoot(io.Discard, io.Discard)
	for _, entry := range flagValueCompletions {
		for _, path := range entry.paths {
			cmd := root
			if path != "" {
				cmd = findCommandPath(root, path)
			}
			require.NotNil(t, cmd, path)
			flag := cmd.Flags().Lookup(entry.flag)
			if path == "" {
				flag = cmd.PersistentFlags().Lookup(entry.flag)
			}
			require.NotNil(t, flag, "%s --%s", path, entry.flag)
		}
	}
	for path := range argValueCompletions {
		require.NotNil(t, findCommandPath(root, path), path)
	}
	for _, path := range append(append([]string{}, profileArgumentPaths...), fileArgumentPaths...) {
		require.NotNil(t, findCommandPath(root, path), path)
	}
}
