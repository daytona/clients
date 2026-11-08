// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package main

import (
	"os"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

// The README's bootstrap commands fetch the installer from the main branch, so
// they need no edit per release. They must work before the first stable 2.x
// release and pick the stable line after it.
const (
	readmeInstallSh  = "https://raw.githubusercontent.com/daytona/clients/main/cli/install.sh"
	readmeInstallPs1 = "https://raw.githubusercontent.com/daytona/clients/main/cli/install.ps1"
)

// readmeCommands returns the README lines that start with prefix and name the
// installer url.
func readmeCommands(t *testing.T, prefix, url string) []string {
	t.Helper()
	readme, err := os.ReadFile("README.md")
	require.NoError(t, err)
	var commands []string
	for line := range strings.Lines(string(readme)) {
		if strings.HasPrefix(line, prefix) && strings.Contains(line, url) {
			commands = append(commands, strings.TrimSpace(line))
		}
	}
	return commands
}

func TestReadmeInstall_bootstrapsFromMainNotFromARelease(t *testing.T) {
	readme, err := os.ReadFile("README.md")
	require.NoError(t, err)

	require.NotContains(t, string(readme), "/releases/download/v", "a pinned release URL needs an edit for every release")
	shell := readmeCommands(t, "curl ", readmeInstallSh)
	require.NotEmpty(t, shell, "README has no install.sh command")
	require.Equal(t, "curl -fsSL "+readmeInstallSh+" | sh", shell[0])
	require.Equal(t, []string{"irm " + readmeInstallPs1 + " | iex"}, readmeCommands(t, "irm ", readmeInstallPs1))
}

func TestReadmeInstall_shellCommandsInstallTheExpectedRelease(t *testing.T) {
	commands := readmeCommands(t, "curl ", readmeInstallSh)
	require.Len(t, commands, 3)
	for _, tc := range []struct {
		name, command string
		index         releaseIndex
		want          string
	}{
		{name: "default before 2.0.0", command: commands[0], index: preGAIndex, want: "2.0.0-alpha.8"},
		{name: "default after 2.0.0", command: commands[0], index: postGAIndex, want: "2.1.0"},
		{name: "alpha channel after 2.0.0", command: commands[1], index: postGAIndex, want: "2.2.0-alpha.1"},
		{name: "exact version", command: commands[2], index: postGAIndex, want: "2.0.0"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// When a user copies the README command.
			out, got, err := runInstallSh(t, tc.command, tc.index)

			// Then the installer fetched from main installs the expected release.
			require.NoError(t, err, out)
			require.Equal(t, tc.want, got, out)
		})
	}
}

func TestReadmeInstall_powershellCommandInstallsTheExpectedRelease(t *testing.T) {
	commands := readmeCommands(t, "irm ", readmeInstallPs1)
	require.Len(t, commands, 1, "README needs exactly one install.ps1 command")
	command := commands[0]
	for _, tc := range []struct {
		name  string
		index releaseIndex
		env   []string
		want  string
	}{
		{name: "default before 2.0.0", index: preGAIndex, want: "2.0.0-alpha.8"},
		{name: "default after 2.0.0", index: postGAIndex, want: "2.1.0"},
		{name: "alpha channel after 2.0.0", index: postGAIndex, env: []string{"DAYTONA_CHANNEL=alpha"}, want: "2.2.0-alpha.1"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			out, got, err := runInstallPs1(t, command, tc.index, tc.env...)

			require.NoError(t, err, out)
			require.NotContains(t, out, "INSTALL_FAILED")
			require.Equal(t, tc.want, got, out)
		})
	}
}
