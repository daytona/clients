// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package apiclient

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/daytona/clients/cli/config"
)

// writeConfig writes a single-profile config.json into dir. Exactly one of token or key
// is set, mirroring how login persists a browser token or an API key.
func writeConfig(t *testing.T, dir, org, token, key string) {
	t.Helper()

	profile := config.Profile{
		Id:                   "initial",
		Name:                 "initial",
		Api:                  config.ServerApi{Url: "https://api.example.test/api"},
		ActiveOrganizationId: &org,
	}

	if key != "" {
		profile.Api.Key = &key
	} else {
		profile.Api.Token = &config.Token{
			AccessToken:  token,
			RefreshToken: "refresh",
			// Far enough out that RefreshTokenIfNeeded short-circuits and the test
			// makes no network call.
			ExpiresAt: time.Now().Add(24 * time.Hour),
		}
	}

	contents, err := json.Marshal(config.Config{ActiveProfileId: "initial", Profiles: []config.Profile{profile}})
	if err != nil {
		t.Fatalf("marshal config: %v", err)
	}

	if err := os.WriteFile(filepath.Join(dir, "config.json"), contents, 0600); err != nil {
		t.Fatalf("write config: %v", err)
	}
}

func isolatedConfigDir(t *testing.T) string {
	t.Helper()

	dir := t.TempDir()
	t.Setenv("DAYTONA_CONFIG_DIR", dir)
	t.Setenv(config.DAYTONA_API_URL_ENV_VAR, "")
	t.Setenv(config.DAYTONA_API_KEY_ENV_VAR, "")

	return dir
}

// The client must reflect the profile on disk on every call, not the profile that
// happened to be active the first time it was built.
func TestGetApiClientReflectsConfigChange(t *testing.T) {
	dir := isolatedConfigDir(t)

	writeConfig(t, dir, "ORG-A", "token-AAAA", "")
	if _, err := GetApiClient(nil, nil); err != nil {
		t.Fatalf("first call: %v", err)
	}

	// Another process runs `daytona organization use ORG-B` or `daytona login`.
	writeConfig(t, dir, "ORG-B", "token-BBBB", "")

	client, err := GetApiClient(nil, nil)
	if err != nil {
		t.Fatalf("second call: %v", err)
	}

	headers := client.GetConfig().DefaultHeader
	if got, want := headers["X-Daytona-Organization-ID"], "ORG-B"; got != want {
		t.Errorf("organization header = %q, want %q", got, want)
	}
	if got, want := headers["Authorization"], "Bearer token-BBBB"; got != want {
		t.Errorf("authorization header = %q, want %q", got, want)
	}
}

// A client built for an API-key profile must never be served to a token profile: the
// two authenticate as different principals and the key carries no organization header.
func TestGetApiClientDoesNotServeApiKeyClientToTokenProfile(t *testing.T) {
	dir := isolatedConfigDir(t)

	writeConfig(t, dir, "ORG-A", "", "dtn_key_for_ORG-A")
	first, err := GetApiClient(nil, nil)
	if err != nil {
		t.Fatalf("api key call: %v", err)
	}
	if got, want := first.GetConfig().DefaultHeader["Authorization"], "Bearer dtn_key_for_ORG-A"; got != want {
		t.Fatalf("api key authorization header = %q, want %q", got, want)
	}

	// The user re-authenticates interactively, which clears the stored key.
	writeConfig(t, dir, "ORG-B", "token-BBBB", "")

	client, err := GetApiClient(nil, nil)
	if err != nil {
		t.Fatalf("token call: %v", err)
	}

	headers := client.GetConfig().DefaultHeader
	if got, want := headers["Authorization"], "Bearer token-BBBB"; got != want {
		t.Errorf("authorization header = %q, want %q", got, want)
	}
	if got, want := headers["X-Daytona-Organization-ID"], "ORG-B"; got != want {
		t.Errorf("organization header = %q, want %q", got, want)
	}
}

// An explicitly passed profile must be honoured rather than replaced by whatever
// profile is active on disk.
func TestGetApiClientHonoursExplicitProfile(t *testing.T) {
	dir := isolatedConfigDir(t)
	writeConfig(t, dir, "ORG-A", "token-AAAA", "")

	if _, err := GetApiClient(nil, nil); err != nil {
		t.Fatalf("first call: %v", err)
	}

	key := "dtn_key_for_ORG-C"
	org := "ORG-C"
	explicit := config.Profile{
		Id:                   "p-ORG-C",
		Api:                  config.ServerApi{Url: "https://other.example.test/api", Key: &key},
		ActiveOrganizationId: &org,
	}

	client, err := GetApiClient(&explicit, nil)
	if err != nil {
		t.Fatalf("explicit profile call: %v", err)
	}

	if got, want := client.GetConfig().DefaultHeader["Authorization"], "Bearer "+key; got != want {
		t.Errorf("authorization header = %q, want %q", got, want)
	}
	if got := client.GetConfig().Servers[0].URL; got != explicit.Api.Url {
		t.Errorf("server url = %q, want %q", got, explicit.Api.Url)
	}
}

// An explicitly passed token profile is used as given: the refresh path acts on the
// active profile, so it must not run for, or leak into, a profile the caller supplied.
func TestGetApiClientExplicitTokenProfileIgnoresActiveProfile(t *testing.T) {
	dir := isolatedConfigDir(t)

	// The active profile carries no credentials at all, so refreshing it would fail.
	// That must not affect a caller who supplied a complete profile of its own.
	unset := `{"activeProfile":"initial","profiles":[{"id":"initial","name":"initial",
		"api":{"url":"https://api.example.test/api","key":null,"token":null},
		"activeOrganizationId":"ORG-A"}]}`
	if err := os.WriteFile(filepath.Join(dir, "config.json"), []byte(unset), 0600); err != nil {
		t.Fatalf("write config: %v", err)
	}

	org := "ORG-Z"
	explicit := config.Profile{
		Id:                   "p-ORG-Z",
		Api:                  config.ServerApi{Url: "https://api.example.test/api"},
		ActiveOrganizationId: &org,
	}
	explicit.Api.Token = &config.Token{
		AccessToken:  "token-EXPLICIT",
		RefreshToken: "refresh",
		ExpiresAt:    time.Now().Add(24 * time.Hour),
	}

	client, err := GetApiClient(&explicit, nil)
	if err != nil {
		t.Fatalf("explicit token profile call: %v", err)
	}

	headers := client.GetConfig().DefaultHeader
	if got, want := headers["Authorization"], "Bearer token-EXPLICIT"; got != want {
		t.Errorf("authorization header = %q, want %q", got, want)
	}
	if got, want := headers["X-Daytona-Organization-ID"], "ORG-Z"; got != want {
		t.Errorf("organization header = %q, want %q", got, want)
	}
}

// Headers passed by a caller such as the MCP server must be applied on every call.
func TestGetApiClientAppliesDefaultHeadersOnEveryCall(t *testing.T) {
	dir := isolatedConfigDir(t)
	writeConfig(t, dir, "ORG-A", "token-AAAA", "")

	if _, err := GetApiClient(nil, nil); err != nil {
		t.Fatalf("first call: %v", err)
	}

	client, err := GetApiClient(nil, map[string]string{DaytonaSourceHeader: "mcp"})
	if err != nil {
		t.Fatalf("second call: %v", err)
	}

	if got, want := client.GetConfig().DefaultHeader[DaytonaSourceHeader], "mcp"; got != want {
		t.Errorf("%s = %q, want %q", DaytonaSourceHeader, got, want)
	}
}
