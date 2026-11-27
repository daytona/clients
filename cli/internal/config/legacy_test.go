// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLegacyConfigFile(t *testing.T) {
	base := t.TempDir()
	prev := userConfigDir
	userConfigDir = func() (string, error) { return base, nil }
	t.Cleanup(func() { userConfigDir = prev })
	noEnv := func(string) string { return "" }

	if _, ok := LegacyConfigFile(noEnv); ok {
		t.Fatal("no config.json: want none")
	}
	// The 0.x default: config.json under <user config dir>/daytona.
	want := filepath.Join(base, "daytona", "config.json")
	if err := os.MkdirAll(filepath.Dir(want), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(want, 0o700); err != nil {
		t.Fatal(err)
	}
	if _, ok := LegacyConfigFile(noEnv); ok {
		t.Fatal("a directory named config.json is not a config file")
	}
	if err := os.Remove(want); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(want, []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	if got, ok := LegacyConfigFile(noEnv); !ok || got != want {
		t.Fatalf("got %q %v, want %q", got, ok, want)
	}
	// DAYTONA_CONFIG_DIR wins, as in both CLIs.
	other := t.TempDir()
	env := func(k string) string {
		if k == "DAYTONA_CONFIG_DIR" {
			return other
		}
		return ""
	}
	if _, ok := LegacyConfigFile(env); ok {
		t.Fatal("DAYTONA_CONFIG_DIR without config.json: want none")
	}
	if err := os.WriteFile(filepath.Join(other, "config.json"), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	if got, ok := LegacyConfigFile(env); !ok || got != filepath.Join(other, "config.json") {
		t.Fatalf("got %q %v", got, ok)
	}
	// A relative DAYTONA_CONFIG_DIR is reported as an absolute path, as the
	// CLI resolves its own directory.
	t.Chdir(filepath.Dir(other))
	rel := func(k string) string {
		if k == "DAYTONA_CONFIG_DIR" {
			return filepath.Base(other)
		}
		return ""
	}
	if got, ok := LegacyConfigFile(rel); !ok || got != filepath.Join(other, "config.json") {
		t.Fatalf("relative dir: got %q %v, want %q", got, ok, filepath.Join(other, "config.json"))
	}
}
