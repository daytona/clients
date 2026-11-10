// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/stretchr/testify/require"
)

func TestWhoami_acceptsStaticKeyContext_whenNamesDifferOnlyInCase(t *testing.T) {
	for _, selection := range [][]string{{"--org", "ACME"}, {"--project", "LAB"}, {"--org", "ACME", "--project", "LaB"}} {
		t.Run(selection[1], func(t *testing.T) {
			// Given: name resolution can fall back to the user's reference.
			f := newFakeAPI(t)
			f.on("GET", "/api-keys/current", 200, projectKeyFixture())
			args := []string{"--api-url", f.srv.URL, "--api-key", "fixture-key", "whoami", "-o", "json"}
			args = append(args, selection...)

			// When
			stdout, stderr, code := run(t, cfgDir(t), nil, args...)

			// Then
			require.Equal(t, 0, code, stderr)
			got := mustJSON(t, stdout)
			require.Equal(t, "22222222-2222-2222-2222-222222222222", got["org_id"])
			require.Equal(t, ga2KeyProject, got["project_id"])
			require.Equal(t, true, got["context_resolved"])
		})
	}
}

func TestLogin_refreshesLegacyProfile_whenAlreadySaved(t *testing.T) {
	for _, selection := range []string{"active", "flag", "env"} {
		t.Run(selection, func(t *testing.T) {
			// Given: older versions permitted spaces in saved profile names.
			d := cfgDir(t)
			const name = "legacy profile"
			store, err := config.Open(d)
			require.NoError(t, err)
			require.NoError(t, store.SaveConfig(&config.Config{
				ActiveProfile: name,
				Profiles:      []config.Profile{{Name: name, APIURL: "https://api.example", Credential: "token"}},
			}))
			args := []string{"login", "--token", "replacement-fixture-key"}
			env := map[string]string{}
			switch selection {
			case "flag":
				args = append(args, "--profile", name)
			case "env":
				env["DAYTONA_PROFILE"] = name
			}

			// When
			_, stderr, code := run(t, d, env, args...)

			// Then
			require.Equal(t, 0, code, stderr)
			creds, err := store.LoadCredentials()
			require.NoError(t, err)
			require.Equal(t, "replacement-fixture-key", creds.Profiles[name].Token)
		})
	}
}

func TestLogin_rejectsLegacyStyleName_whenProfileIsNew(t *testing.T) {
	// Given
	d := cfgDir(t)

	// When
	_, stderr, code := run(t, d, nil, "login", "--profile", "new profile", "--token", "fixture-key")

	// Then
	require.Equal(t, cerr.ExitUsage, code, stderr)
	_, err := os.Stat(filepath.Join(d, "credentials.yaml"))
	require.ErrorIs(t, err, os.ErrNotExist)
}
