// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package app

import (
	"context"
	"io"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestLoad_rejectsAPIURLDelimiters_whenQueryOrFragmentIsEmpty(t *testing.T) {
	for _, suffix := range []string{"?", "#", "?#"} {
		t.Run(suffix, func(t *testing.T) {
			// Given
			dir := t.TempDir()
			getenv := func(key string) string {
				if key == "DAYTONA_CONFIG_DIR" {
					return dir
				}
				return ""
			}

			// When
			_, err := Load(Globals{APIURL: "https://api.example" + suffix}, io.Discard, io.Discard, getenv)

			// Then
			require.Error(t, err)
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
		})
	}
}

func TestLoad_acceptsAPIURL_whenPathContainsEscapedDelimiter(t *testing.T) {
	// Given
	dir := t.TempDir()
	getenv := func(key string) string {
		if key == "DAYTONA_CONFIG_DIR" {
			return dir
		}
		return ""
	}

	// When
	a, err := Load(Globals{APIURL: "https://api.example/base%23part%3Fpart"}, io.Discard, io.Discard, getenv)

	// Then
	require.NoError(t, err)
	require.Equal(t, "https://api.example/base%23part%3Fpart", a.APIURL())
}

func TestWorkloadClient_acceptsStaticKeyProject_whenNameDiffersOnlyInCase(t *testing.T) {
	for _, ref := range []string{"DeMo", "another-project"} {
		t.Run(ref, func(t *testing.T) {
			// Given: without an organization, a project name remains unresolved.
			dir := t.TempDir()
			getenv := func(key string) string {
				if key == "DAYTONA_CONFIG_DIR" {
					return dir
				}
				return ""
			}
			a, err := Load(Globals{APIKeySet: true, APIKey: "fixture-key", Project: ref}, io.Discard, io.Discard, getenv)
			require.NoError(t, err)
			a.StaticKeyProjectFn = func(context.Context) (StaticKeyProject, bool, error) {
				return StaticKeyProject{ID: "22222222-2222-2222-2222-222222222222", Name: "demo"}, true, nil
			}

			// When
			client, _, err := a.WorkloadClient(context.Background(), nil)

			// Then
			if ref == "another-project" {
				require.Error(t, err)
				require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
				return
			}
			require.NoError(t, err)
			require.Equal(t, "fixture-key", client.Credential)
		})
	}
}
