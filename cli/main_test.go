// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
)

// The CLI resolves its API endpoint, identity provider and config directory from
// environment variables. Nothing may promote a file found in the current working
// directory into that environment: a cloned repository would then be able to point
// the CLI, and the bearer credential it sends, at a host of its choosing.
//
// These tests drive the built binary rather than calling into a package. The
// bootstrap being guarded lives in main(), so an in-process test would pass
// regardless of what main() does.

const (
	testVersion    = "v0.0.0-regress"
	testDefaultURL = "https://api.example.test/api"
)

// buildCLI compiles the CLI with release-shaped build info and returns its path.
//
// The version must not be "v0.0.0-dev": that value makes the login path override
// the configured API URL with a localhost default, which would mask exactly the
// behaviour TestCwdDotenvIgnoredOnLogin asserts.
func buildCLI(t *testing.T) string {
	t.Helper()

	bin := filepath.Join(t.TempDir(), "daytona-under-test")
	ldflags := "-X github.com/daytona/clients/cli/internal.Version=" + testVersion +
		" -X github.com/daytona/clients/cli/internal.DaytonaApiUrl=" + testDefaultURL

	cmd := exec.Command("go", "build", "-ldflags", ldflags, "-o", bin, ".")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("building CLI under test: %v\n%s", err, out)
	}

	return bin
}

// runCLI runs the binary from workDir with a minimal environment, so that no
// DAYTONA_* or proxy variable set on the developer's machine can affect the result.
func runCLI(t *testing.T, bin, workDir, configDir string, args ...string) string {
	t.Helper()

	cmd := exec.Command(bin, args...)
	cmd.Dir = workDir
	cmd.Env = []string{
		"PATH=" + os.Getenv("PATH"),
		"HOME=" + t.TempDir(),
		"DAYTONA_CONFIG_DIR=" + configDir,
	}

	out, _ := cmd.CombinedOutput() // these invocations are expected to fail
	return string(out)
}

func writeDotenv(t *testing.T, dir, contents string) {
	t.Helper()

	if err := os.WriteFile(filepath.Join(dir, ".env"), []byte(contents), 0600); err != nil {
		t.Fatalf("writing .env fixture: %v", err)
	}
}

// TestCwdDotenvNeverReceivesCredential asserts that credentials declared by a .env
// file in the working directory are not used, and that no request reaches the host
// that file names.
func TestCwdDotenvNeverReceivesCredential(t *testing.T) {
	if testing.Short() {
		t.Skip("builds the CLI binary")
	}

	var requests int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt64(&requests, 1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"unexpected":true}`))
	}))
	defer server.Close()

	bin := buildCLI(t)
	workDir := t.TempDir()
	writeDotenv(t, workDir, "DAYTONA_API_KEY=regression-secret\nDAYTONA_API_URL="+server.URL+"/api\n")

	out := runCLI(t, bin, workDir, t.TempDir(), "list")

	if got := atomic.LoadInt64(&requests); got != 0 {
		t.Errorf("host named by the working directory .env received %d request(s), want 0; CLI output:\n%s", got, out)
	}

	if !strings.Contains(out, "no profiles found") {
		t.Errorf("want the CLI to report no configured profile, got:\n%s", out)
	}
}

// TestCwdDotenvIgnoredOnLogin asserts that an API URL declared by a .env file in the
// working directory is not persisted into the profile written at first login. A
// persisted value would outlive the directory and redirect every later authenticated
// request.
func TestCwdDotenvIgnoredOnLogin(t *testing.T) {
	if testing.Short() {
		t.Skip("builds the CLI binary")
	}

	bin := buildCLI(t)
	workDir := t.TempDir()
	configDir := t.TempDir()

	// Only the URL: the login path reads it through a separate resolver that does
	// not require an accompanying API key.
	writeDotenv(t, workDir, "DAYTONA_API_URL=https://unexpected.example/api\n")

	out := runCLI(t, bin, workDir, configDir, "login", "--api-key", "regression-secret")

	raw, err := os.ReadFile(filepath.Join(configDir, "config.json"))
	if err != nil {
		t.Fatalf("reading persisted config: %v\nCLI output:\n%s", err, out)
	}

	var persisted struct {
		Profiles []struct {
			Id  string `json:"id"`
			Api struct {
				Url string `json:"url"`
			} `json:"api"`
		} `json:"profiles"`
	}
	if err := json.Unmarshal(raw, &persisted); err != nil {
		t.Fatalf("parsing persisted config: %v\n%s", err, raw)
	}

	if len(persisted.Profiles) == 0 {
		t.Fatalf("no profile was persisted; CLI output:\n%s", out)
	}

	for _, profile := range persisted.Profiles {
		if profile.Api.Url != testDefaultURL {
			t.Errorf("profile %q persisted API URL %q, want the built-in default %q",
				profile.Id, profile.Api.Url, testDefaultURL)
		}
	}
}
