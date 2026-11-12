// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package config

import (
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestUpsert_preservesLegacyNamesOnly_whenReplacingExistingProfile(t *testing.T) {
	for _, existing := range []bool{true, false} {
		name := "new"
		if existing {
			name = "existing"
		}
		t.Run(name, func(t *testing.T) {
			// Given
			original := Profile{Name: "legacy name/x", APIURL: "https://old.example", Credential: "token"}
			replacement := Profile{Name: original.Name, APIURL: "https://new.example", Credential: "oidc"}
			cfg := &Config{}
			if existing {
				cfg.Profiles = []Profile{original}
			}

			// When
			err := cfg.Upsert(replacement)

			// Then
			if existing {
				require.NoError(t, err)
				require.Equal(t, []Profile{replacement}, cfg.Profiles)
				return
			}
			require.Error(t, err)
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
			require.Empty(t, cfg.Profiles)
		})
	}
}
