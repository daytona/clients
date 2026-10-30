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
	"regexp"
	"strings"
	"time"

	"github.com/daytona/clients/cli/config"
	"github.com/daytona/clients/cli/internal"
)

// The GitHub releases that carry the CLI binaries. Overridden in tests.
//
// releases/latest is not used: the repository releases more than one major
// line, and its "Latest" release can belong to another line than this CLI.
var latestReleaseURL = "https://api.github.com/repos/daytona/clients/releases?per_page=100"

const (
	latestReleaseCacheFile = "latest-cli-version.json"
	latestReleaseCacheTTL  = 24 * time.Hour
	latestReleaseTimeout   = 3 * time.Second
)

type latestReleaseCache struct {
	Version   string    `json:"version"`
	CheckedAt time.Time `json:"checkedAt"`
}

// latestCliVersion returns the newest published stable CLI version of this
// CLI's major line, without a "v" prefix.
//
// GitHub is consulted at most once per latestReleaseCacheTTL; the result is cached
// in the config directory. A failed refresh keeps serving the previously cached
// version and is itself cached, so an offline machine does not retry on every
// command. An error is returned only when no version is known at all.
func latestCliVersion() (string, error) {
	cachePath, cached := readLatestReleaseCache()
	// A cache written by a CLI of another major line shares the config
	// directory but not the version line.
	if cached != nil && cached.Version != "" && !sameMajor(cached.Version, cliVersion()) {
		cached = nil
	}
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

	var releases []struct {
		TagName    string `json:"tag_name"`
		Draft      bool   `json:"draft"`
		Prerelease bool   `json:"prerelease"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&releases); err != nil {
		return "", err
	}

	current := cliVersion()
	latest := ""
	for _, release := range releases {
		version := strings.TrimPrefix(strings.TrimSpace(release.TagName), "v")
		if release.Draft || release.Prerelease || !stableVersionPattern.MatchString(version) || !sameMajor(version, current) {
			continue
		}
		if latest == "" || compareVersions(version, latest) > 0 {
			latest = version
		}
	}
	if latest == "" {
		return "", fmt.Errorf("no stable release of the %s.x line found", majorOf(current))
	}
	return latest, nil
}

var stableVersionPattern = regexp.MustCompile(`^[0-9]+\.[0-9]+\.[0-9]+$`)

func cliVersion() string {
	return strings.TrimPrefix(internal.Version, "v")
}

func majorOf(version string) string {
	major, _, _ := strings.Cut(version, ".")
	return major
}

func sameMajor(v1, v2 string) bool {
	return majorOf(v1) == majorOf(v2)
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
