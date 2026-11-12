// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"strconv"
	"strings"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/stretchr/testify/require"
)

func TestProfileNames_rejectedWhenInvalid(t *testing.T) {
	for _, name := range []string{"", ".", "..", "bad/name", "bad name", "bad\nname", strings.Repeat("a", 65)} {
		for _, command := range [][]string{{"login", "--token", "fixture", "--profile", name}, {"profile", "use", name}} {
			t.Run(strings.Join(command, " "), func(t *testing.T) {
				// Given
				dir := cfgDir(t)
				// When
				_, stderr, code := run(t, dir, nil, command...)
				// Then
				require.Equal(t, cerr.ExitUsage, code, stderr)
			})
		}
	}
}

func TestProfileList_omitsCredentialWhenEmpty(t *testing.T) {
	// Given
	dir := cfgDir(t)
	store, err := config.Open(dir)
	require.NoError(t, err)
	require.NoError(t, store.SaveConfig(&config.Config{Profiles: []config.Profile{{Name: "default"}}}))
	// When
	stdout, stderr, code := run(t, dir, nil, "profile", "list", "-o", "json")
	// Then
	require.Zero(t, code, stderr)
	items := mustJSON(t, stdout)["items"].([]any)
	require.NotContains(t, items[0].(map[string]any), "credential")
}

func TestProfileDelete_announcesSelectionWhenActive(t *testing.T) {
	for _, hasDefault := range []bool{false, true} {
		t.Run(strconv.FormatBool(hasDefault), func(t *testing.T) {
			// Given
			dir := cfgDir(t)
			store, err := config.Open(dir)
			require.NoError(t, err)
			cfg := &config.Config{ActiveProfile: "work", Profiles: []config.Profile{{Name: "work"}}}
			if hasDefault {
				cfg.Profiles = append(cfg.Profiles, config.Profile{Name: "default"})
			}
			require.NoError(t, store.SaveConfig(cfg))
			// When
			stdout, stderr, code := run(t, dir, nil, "profile", "delete", "work", "--yes")
			// Then
			require.Zero(t, code, stderr)
			require.Empty(t, stdout)
			require.Contains(t, stderr, "profile")
			if hasDefault {
				require.Contains(t, stderr, `"default"`)
			} else {
				require.Contains(t, stderr, "none")
			}
		})
	}
}

func TestProfileOptionalCommands_workWhenActiveProfileMissing(t *testing.T) {
	for _, args := range [][]string{{"profile", "list"}, {"config", "get", "confirm"}} {
		t.Run(strings.Join(args, " "), func(t *testing.T) {
			// Given
			dir := cfgDir(t)
			store, err := config.Open(dir)
			require.NoError(t, err)
			require.NoError(t, store.SaveConfig(&config.Config{ActiveProfile: "missing"}))
			// When
			stdout, stderr, code := run(t, dir, nil, args...)
			// Then
			require.Zero(t, code, stderr)
			require.NotEmpty(t, mustJSON(t, stdout))
		})
	}
}
