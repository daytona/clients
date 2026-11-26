// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
)

// writeLegacyConfig writes a 0.x CLI config.json into d, the directory both
// CLIs read when DAYTONA_CONFIG_DIR names it.
func writeLegacyConfig(t *testing.T, d string) string {
	t.Helper()
	path := filepath.Join(d, "config.json")
	require.NoError(t, os.WriteFile(path, []byte(`{"activeProfile":"p1","profiles":[{"id":"p1","name":"default","api":{"url":"https://app.daytona.io/api","key":"0x-key"}}]}`), 0o600))
	return path
}

// With only the 0.x CLI's config.json and no 2.x profile, "not logged in"
// points at daytona-v0 and at `daytona login`, on management and workload
// commands alike, and whoami notes it on stderr.
func TestNotLoggedInPointsAtTheV0CLIWhenOnlyItsConfigExists(t *testing.T) {
	d := cfgDir(t)
	legacy := writeLegacyConfig(t, d)
	for _, args := range [][]string{{"org", "list"}, {"sandbox", "list"}} {
		_, errb, code := run(t, d, nil, append(args, "--api-url", "http://127.0.0.1:1")...)
		require.Equal(t, cerr.ExitAuth, code, errb)
		require.Contains(t, errb, "not logged in")
		require.Contains(t, errb, "daytona login")
		require.Contains(t, errb, "daytona-v0")
		require.Contains(t, errb, "daytonaio/cli/daytona@0")
		require.Contains(t, errb, legacy)
	}
	stdout, errb, code := run(t, d, nil, "whoami", "--api-url", "http://127.0.0.1:1")
	require.Equal(t, 0, code, errb)
	require.NotContains(t, mustJSON(t, stdout), "credential_source")
	require.Contains(t, errb, "daytona-v0")
	require.Contains(t, errb, "daytona login")

	_, errb, code = run(t, d, nil, "whoami", "--quiet", "--api-url", "http://127.0.0.1:1")
	require.Equal(t, 0, code, errb)
	require.NotContains(t, errb, "daytona-v0")

	// The 0.x file is only pointed at: never read, changed or turned into a profile.
	raw, err := os.ReadFile(legacy)
	require.NoError(t, err)
	require.Contains(t, string(raw), `"0x-key"`)
	cfg, err := (&config.Store{Dir: d}).LoadConfig()
	require.NoError(t, err)
	require.Empty(t, cfg.Profiles)
}

// A 2.x profile means the user already set this CLI up: no 0.x hint, even
// when the selected profile holds no credential.
func TestNotLoggedInHasNoV0HintOnceA2xProfileExists(t *testing.T) {
	d := cfgDir(t)
	writeLegacyConfig(t, d)
	require.NoError(t, (&config.Store{Dir: d}).SaveConfig(&config.Config{Profiles: []config.Profile{{Name: "work"}}}))
	_, errb, code := run(t, d, nil, "org", "list", "--api-url", "http://127.0.0.1:1")
	require.Equal(t, cerr.ExitAuth, code, errb)
	require.Contains(t, errb, "not logged in")
	require.NotContains(t, errb, "daytona-v0")
	_, errb, code = run(t, d, nil, "whoami", "--api-url", "http://127.0.0.1:1")
	require.Equal(t, 0, code, errb)
	require.NotContains(t, errb, "daytona-v0")
}

// Without a 0.x config.json the error is the plain one.
func TestNotLoggedInHasNoV0HintWithoutItsConfig(t *testing.T) {
	_, errb, code := run(t, cfgDir(t), nil, "org", "list", "--api-url", "http://127.0.0.1:1")
	require.Equal(t, cerr.ExitAuth, code, errb)
	require.Contains(t, errb, "not logged in")
	require.NotContains(t, errb, "daytona-v0")
}
