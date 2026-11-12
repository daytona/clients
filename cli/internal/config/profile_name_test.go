// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package config

import (
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestUpsert_refusesInvalidNamesBeforeMutation(t *testing.T) {
	for _, name := range []string{"", ".", "..", "bad/name", "bad name", "bad\nname", strings.Repeat("a", 65)} {
		t.Run(name, func(t *testing.T) {
			// Given
			cfg := &Config{}
			// When
			err := cfg.Upsert(Profile{Name: name})
			// Then
			require.Error(t, err)
			require.Empty(t, cfg.Profiles)
		})
	}
}

func TestUpsert_acceptsValidNames(t *testing.T) {
	for _, name := range []string{"a", "A_Z-0.9", strings.Repeat("a", 64)} {
		t.Run(name, func(t *testing.T) {
			// Given
			cfg := &Config{}
			// When
			err := cfg.Upsert(Profile{Name: name})
			// Then
			require.NoError(t, err)
			require.Equal(t, []Profile{{Name: name}}, cfg.Profiles)
		})
	}
}

func TestLoadConfig_preservesLegacyNames(t *testing.T) {
	// Given
	store, err := Open(t.TempDir())
	require.NoError(t, err)
	cfg := &Config{ActiveProfile: "old name/x", Profiles: []Profile{{Name: "old name/x"}}}
	require.NoError(t, store.SaveConfig(cfg))
	// When
	loaded, err := store.LoadConfig()
	// Then
	require.NoError(t, err)
	require.Equal(t, cfg, loaded)
}

func TestRemove_selectsDefaultWhenActiveRemoved(t *testing.T) {
	// Given
	cfg := &Config{ActiveProfile: "work", Profiles: []Profile{{Name: "work"}, {Name: "default"}}}
	// When
	removed := cfg.Remove("work")
	// Then
	require.True(t, removed)
	require.Equal(t, "default", cfg.ActiveProfile)
}
