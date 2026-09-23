// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package apiclient

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/daytona/clients/cli/config"
	"github.com/daytona/clients/cli/internal"
)

// The GitHub release that carries the CLI binaries. Overridden in tests.
var latestReleaseURL = "https://api.github.com/repos/daytona/clients/releases/latest"

const (
	latestReleaseCacheFile = "latest-cli-version.json"
	latestReleaseCacheTTL  = 24 * time.Hour
	latestReleaseTimeout   = 3 * time.Second
)

type latestReleaseCache struct {
	Version   string    `json:"version"`
	CheckedAt time.Time `json:"checkedAt"`
}

// latestCliVersion returns the newest published CLI version without a "v" prefix.
//
// GitHub is consulted at most once per latestReleaseCacheTTL; the result is cached
// in the config directory. A failed refresh keeps serving the previously cached
// version and is itself cached, so an offline machine does not retry on every
// command. An error is returned only when no version is known at all.
func latestCliVersion() (string, error) {
	cachePath, cached := readLatestReleaseCache()
	if cached != nil && isFreshCache(cached.CheckedAt) {
		if cached.Version == "" {
			return "", errors.New("latest CLI version unknown")
		}
		return cached.Version, nil
	}

	version, err := fetchLatestCliVersion()
	if err != nil {
		if cached != nil && cached.Version != "" {
			version = cached.Version
		} else {
			version = ""
		}
	}

	if cachePath != "" {
		writeLatestReleaseCache(cachePath, latestReleaseCache{Version: version, CheckedAt: time.Now()})
	}

	if version == "" {
		return "", fmt.Errorf("fetch latest CLI version: %w", err)
	}
	return version, nil
}

// A timestamp in the future means the clock moved backwards or the cache
// came from another machine; treat it as stale rather than trusting it.
func isFreshCache(checkedAt time.Time) bool {
	age := time.Since(checkedAt)
	return age >= 0 && age < latestReleaseCacheTTL
}

func fetchLatestCliVersion() (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), latestReleaseTimeout)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, latestReleaseURL, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("User-Agent", "daytona-cli/"+internal.Version)

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("unexpected status %d", resp.StatusCode)
	}

	var release struct {
		TagName string `json:"tag_name"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&release); err != nil {
		return "", err
	}

	version := strings.TrimPrefix(strings.TrimSpace(release.TagName), "v")
	if version == "" {
		return "", errors.New("release has no tag_name")
	}
	return version, nil
}

// readLatestReleaseCache returns the cache path (empty if the config dir is
// unavailable) and the cached entry (nil if absent or unreadable).
func readLatestReleaseCache() (string, *latestReleaseCache) {
	configDir, err := config.GetConfigDir()
	if err != nil {
		return "", nil
	}
	path := filepath.Join(configDir, latestReleaseCacheFile)

	contents, err := os.ReadFile(path)
	if err != nil {
		return path, nil
	}

	var cached latestReleaseCache
	if err := json.Unmarshal(contents, &cached); err != nil {
		return path, nil
	}
	return path, &cached
}

// writeLatestReleaseCache is best effort: the cache only saves network calls.
func writeLatestReleaseCache(path string, entry latestReleaseCache) {
	contents, err := json.Marshal(entry)
	if err != nil {
		return
	}
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return
	}
	_ = os.WriteFile(path, contents, 0600)
}
