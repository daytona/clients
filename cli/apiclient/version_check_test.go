// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package apiclient

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal"
	log "github.com/sirupsen/logrus"
)

type versionCheckFixture struct {
	dir      string
	logs     *bytes.Buffer
	requests *atomic.Int32
}

// newVersionCheckFixture pins the CLI to cliVersion and points the release lookup at
// a fake GitHub that answers with latestTag (or the given status when latestTag is "").
func newVersionCheckFixture(t *testing.T, cliVersion, latestTag string, status int) versionCheckFixture {
	t.Helper()

	dir := isolatedConfigDir(t)

	requests := &atomic.Int32{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		if latestTag == "" {
			w.WriteHeader(status)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{"tag_name": latestTag})
	}))
	t.Cleanup(server.Close)

	prevURL := latestReleaseURL
	latestReleaseURL = server.URL
	prevVersion := internal.Version
	internal.Version = cliVersion
	prevSuppress := internal.SuppressVersionMismatchWarning
	internal.SuppressVersionMismatchWarning = false
	versionMismatchWarningOnce = sync.Once{}

	logs := &bytes.Buffer{}
	prevOut := log.StandardLogger().Out
	log.SetOutput(logs)

	t.Cleanup(func() {
		latestReleaseURL = prevURL
		internal.Version = prevVersion
		internal.SuppressVersionMismatchWarning = prevSuppress
		versionMismatchWarningOnce = sync.Once{}
		log.SetOutput(prevOut)
	})

	return versionCheckFixture{dir: dir, logs: logs, requests: requests}
}

func apiResponse(apiVersion string) *http.Response {
	req := httptest.NewRequest(http.MethodGet, "https://api.example.test/api/sandbox", nil)
	res := &http.Response{StatusCode: http.StatusOK, Header: http.Header{}, Request: req}
	if apiVersion != "" {
		res.Header.Set(API_VERSION_HEADER, apiVersion)
	}
	return res
}

func (f versionCheckFixture) writeCache(t *testing.T, version string, checkedAt time.Time) {
	t.Helper()
	writeLatestReleaseCache(filepath.Join(f.dir, latestReleaseCacheFile), latestReleaseCache{Version: version, CheckedAt: checkedAt})
}

func (f versionCheckFixture) readCache(t *testing.T) latestReleaseCache {
	t.Helper()
	contents, err := os.ReadFile(filepath.Join(f.dir, latestReleaseCacheFile))
	if err != nil {
		t.Fatalf("read cache: %v", err)
	}
	var cached latestReleaseCache
	if err := json.Unmarshal(contents, &cached); err != nil {
		t.Fatalf("unmarshal cache: %v", err)
	}
	return cached
}

// The reported case: the API was released without a CLI release, so the user is
// already on the newest CLI and must not be told to upgrade.
func TestVersionCheckSilentWhenCliIsLatestRelease(t *testing.T) {
	f := newVersionCheckFixture(t, "v0.215.0", "v0.215.0", http.StatusOK)

	checkVersionsMismatch(apiResponse("0.216.0"))

	if f.logs.Len() != 0 {
		t.Errorf("expected no warning, got %q", f.logs.String())
	}
	if got := f.requests.Load(); got != 1 {
		t.Errorf("release lookups = %d, want 1", got)
	}
}

func TestVersionCheckWarnsWhenNewerCliReleased(t *testing.T) {
	f := newVersionCheckFixture(t, "v0.215.0", "v0.216.0", http.StatusOK)

	checkVersionsMismatch(apiResponse("0.216.0"))

	out := f.logs.String()
	if !strings.Contains(out, "Daytona CLI v0.215.0 is outdated, the latest version is v0.216.0") {
		t.Errorf("unexpected warning output %q", out)
	}
	if !strings.Contains(out, "brew upgrade daytonaio/cli/daytona") {
		t.Errorf("warning must include upgrade instructions, got %q", out)
	}
}

// Only the API header can tell us cheaply that something might be off; when the CLI
// is not behind the API we must not spend a network call on GitHub.
func TestVersionCheckSkipsReleaseLookupWhenCliNotBehindApi(t *testing.T) {
	f := newVersionCheckFixture(t, "v0.216.0", "v0.217.0", http.StatusOK)

	checkVersionsMismatch(apiResponse("0.216.0"))
	checkVersionsMismatch(apiResponse("0.215.0"))
	checkVersionsMismatch(apiResponse(""))

	if f.logs.Len() != 0 {
		t.Errorf("expected no warning, got %q", f.logs.String())
	}
	if got := f.requests.Load(); got != 0 {
		t.Errorf("release lookups = %d, want 0", got)
	}
}

func TestVersionCheckSkipsDevBuilds(t *testing.T) {
	f := newVersionCheckFixture(t, "v0.0.0-dev", "v0.216.0", http.StatusOK)

	checkVersionsMismatch(apiResponse("0.216.0"))

	if f.logs.Len() != 0 || f.requests.Load() != 0 {
		t.Errorf("dev build must neither warn nor look up releases; logs=%q lookups=%d", f.logs.String(), f.requests.Load())
	}
}

func TestVersionCheckRespectsStructuredOutputSuppression(t *testing.T) {
	f := newVersionCheckFixture(t, "v0.215.0", "v0.216.0", http.StatusOK)
	internal.SuppressVersionMismatchWarning = true

	checkVersionsMismatch(apiResponse("0.216.0"))

	if f.logs.Len() != 0 || f.requests.Load() != 0 {
		t.Errorf("suppressed mode must neither warn nor look up releases; logs=%q lookups=%d", f.logs.String(), f.requests.Load())
	}
}

func TestVersionCheckWarnsOnlyOncePerProcess(t *testing.T) {
	f := newVersionCheckFixture(t, "v0.215.0", "v0.216.0", http.StatusOK)

	checkVersionsMismatch(apiResponse("0.216.0"))
	checkVersionsMismatch(apiResponse("0.216.0"))

	if got := strings.Count(f.logs.String(), "is outdated"); got != 1 {
		t.Errorf("warnings = %d, want 1; output %q", got, f.logs.String())
	}
	if got := f.requests.Load(); got != 1 {
		t.Errorf("release lookups = %d, want 1", got)
	}
}

// A user without access to GitHub must not see a bogus warning, and must not pay for
// a failing request on every command.
func TestVersionCheckSilentAndCachesWhenGitHubUnavailable(t *testing.T) {
	f := newVersionCheckFixture(t, "v0.215.0", "", http.StatusForbidden)

	checkVersionsMismatch(apiResponse("0.216.0"))

	if f.logs.Len() != 0 {
		t.Errorf("expected no warning, got %q", f.logs.String())
	}
	cached := f.readCache(t)
	if cached.Version != "" {
		t.Errorf("cached version = %q, want empty", cached.Version)
	}
	if time.Since(cached.CheckedAt) > time.Minute {
		t.Errorf("cache checkedAt not refreshed: %v", cached.CheckedAt)
	}

	versionMismatchWarningOnce = sync.Once{}
	checkVersionsMismatch(apiResponse("0.216.0"))
	if got := f.requests.Load(); got != 1 {
		t.Errorf("release lookups = %d, want 1 (failure must be cached)", got)
	}
}

func TestVersionCheckUsesFreshCacheWithoutNetwork(t *testing.T) {
	f := newVersionCheckFixture(t, "v0.215.0", "v0.999.0", http.StatusOK)
	f.writeCache(t, "0.216.0", time.Now().Add(-time.Hour))

	checkVersionsMismatch(apiResponse("0.216.0"))

	if !strings.Contains(f.logs.String(), "latest version is v0.216.0") {
		t.Errorf("expected cached version in warning, got %q", f.logs.String())
	}
	if got := f.requests.Load(); got != 0 {
		t.Errorf("release lookups = %d, want 0", got)
	}
}

func TestVersionCheckRefreshesExpiredCache(t *testing.T) {
	f := newVersionCheckFixture(t, "v0.215.0", "v0.217.0", http.StatusOK)
	f.writeCache(t, "0.216.0", time.Now().Add(-latestReleaseCacheTTL-time.Minute))

	checkVersionsMismatch(apiResponse("0.217.0"))

	if !strings.Contains(f.logs.String(), "latest version is v0.217.0") {
		t.Errorf("expected refreshed version in warning, got %q", f.logs.String())
	}
	if got := f.requests.Load(); got != 1 {
		t.Errorf("release lookups = %d, want 1", got)
	}
	if got := f.readCache(t).Version; got != "0.217.0" {
		t.Errorf("cached version = %q, want 0.217.0", got)
	}
}

// A stale but known version beats no version when the refresh fails.
func TestVersionCheckFallsBackToStaleCacheWhenRefreshFails(t *testing.T) {
	f := newVersionCheckFixture(t, "v0.215.0", "", http.StatusInternalServerError)
	f.writeCache(t, "0.216.0", time.Now().Add(-2*latestReleaseCacheTTL))

	checkVersionsMismatch(apiResponse("0.216.0"))

	if !strings.Contains(f.logs.String(), "latest version is v0.216.0") {
		t.Errorf("expected stale cached version in warning, got %q", f.logs.String())
	}
	if got := f.readCache(t).Version; got != "0.216.0" {
		t.Errorf("stale version must be kept in cache, got %q", got)
	}
}

func TestVersionCheckIgnoresCorruptCache(t *testing.T) {
	f := newVersionCheckFixture(t, "v0.215.0", "v0.216.0", http.StatusOK)
	if err := os.WriteFile(filepath.Join(f.dir, latestReleaseCacheFile), []byte("{not json"), 0600); err != nil {
		t.Fatalf("write corrupt cache: %v", err)
	}

	checkVersionsMismatch(apiResponse("0.216.0"))

	if !strings.Contains(f.logs.String(), "latest version is v0.216.0") {
		t.Errorf("expected warning after corrupt cache, got %q", f.logs.String())
	}
	if got := f.readCache(t).Version; got != "0.216.0" {
		t.Errorf("cache must be rewritten, got %q", got)
	}
}
