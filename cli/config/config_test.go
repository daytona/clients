// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package config

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
)

func TestConfigIgnoresLegacyToolboxProxyUrlsKey(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("DAYTONA_CONFIG_DIR", dir)
	t.Setenv(DAYTONA_API_URL_ENV_VAR, "")
	t.Setenv(DAYTONA_API_KEY_ENV_VAR, "")

	configPath := filepath.Join(dir, "config.json")
	legacy := `{
  "activeProfile": "default",
  "profiles": [
    {
      "id": "default",
      "name": "default",
      "api": {
        "url": "https://api.example.test",
        "key": "test-api-key",
        "token": null
      },
      "activeOrganizationId": "org-123",
      "toolboxProxyUrls": {
        "us": "https://proxy.example.test/toolbox"
      }
    }
  ]
}`
	if err := os.WriteFile(configPath, []byte(legacy), 0600); err != nil {
		t.Fatalf("write legacy config: %v", err)
	}

	c, err := GetConfig()
	if err != nil {
		t.Fatalf("GetConfig() error = %v", err)
	}
	if err := c.Save(); err != nil {
		t.Fatalf("Save() error = %v", err)
	}

	contents, err := os.ReadFile(configPath)
	if err != nil {
		t.Fatalf("read saved config: %v", err)
	}
	if strings.Contains(strings.ToLower(string(contents)), "toolboxproxyurl") {
		t.Fatalf("expected legacy key to be dropped, got:\n%s", contents)
	}

	profile, err := c.GetActiveProfile()
	if err != nil {
		t.Fatalf("GetActiveProfile() error = %v", err)
	}
	if profile.Id != "default" {
		t.Fatalf("expected profile id default, got %q", profile.Id)
	}
	if profile.Name != "default" {
		t.Fatalf("expected profile name default, got %q", profile.Name)
	}
	if profile.Api.Url != "https://api.example.test" {
		t.Fatalf("expected api url to be preserved, got %q", profile.Api.Url)
	}
	if profile.Api.Key == nil || *profile.Api.Key != "test-api-key" {
		t.Fatalf("expected api key to be preserved, got %v", profile.Api.Key)
	}
	if profile.ActiveOrganizationId == nil || *profile.ActiveOrganizationId != "org-123" {
		t.Fatalf("expected organization id to be preserved, got %v", profile.ActiveOrganizationId)
	}
}

func TestSaveNeverExposesAPartialConfigToAConcurrentReader(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("DAYTONA_CONFIG_DIR", dir)
	t.Setenv(DAYTONA_API_URL_ENV_VAR, "")
	t.Setenv(DAYTONA_API_KEY_ENV_VAR, "")

	c := &Config{ActiveProfileId: "default", Profiles: []Profile{{Id: "default", Name: "default"}}}
	if err := c.Save(); err != nil {
		t.Fatal(err)
	}

	const rounds = 200
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		for i := 0; i < rounds; i++ {
			if err := c.Save(); err != nil {
				t.Errorf("Save() error = %v", err)
				return
			}
		}
	}()

	// Join the writer before failing: after the test returns, t.Setenv restores the
	// real DAYTONA_CONFIG_DIR and a still-running Save would overwrite the user's config.
	var readErr error
	for i := 0; i < rounds && readErr == nil; i++ {
		_, readErr = GetConfig()
	}
	wg.Wait()
	if readErr != nil {
		t.Fatalf("a concurrent GetConfig() saw a partial file: %v", readErr)
	}

	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || entries[0].Name() != "config.json" {
		t.Errorf("expected only config.json in the config dir, got %v", entries)
	}

	if runtime.GOOS != "windows" {
		info, err := os.Stat(filepath.Join(dir, "config.json"))
		if err != nil {
			t.Fatal(err)
		}
		if info.Mode().Perm() != 0600 {
			t.Errorf("config mode = %v, want 0600", info.Mode().Perm())
		}
	}
}
