// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package config

import (
	"os"
	"path/filepath"
	"strings"
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
