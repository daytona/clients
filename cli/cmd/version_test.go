// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
)

func TestVersion_printsConnectedAPIBuildIdentity(t *testing.T) {
	api := newFakeAPI(t)
	api.on("GET", "/config", 200, versionConfigFixture(api.srv.URL, "2.0.0-alpha.4", "0123456789abcdef0123456789abcdef01234567", "2026-10-02T14:03:11Z"))

	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "-o", "table", "version")
	if code != 0 {
		t.Fatalf("code=%d stderr=%s", code, stderr)
	}
	if stderr != "" {
		t.Fatalf("stderr=%q", stderr)
	}
	for _, want := range []string{
		"Connected API: " + api.srv.URL,
		"Version: 2.0.0-alpha.4",
		"Build SHA: 0123456789abcdef0123456789abcdef01234567",
		"Built At: 2026-10-02T14:03:11Z",
	} {
		if !has(stdout, want) {
			t.Fatalf("stdout missing %q:\n%s", want, stdout)
		}
	}

	findRequest(t, api, "GET", "/config")
	jsonStdout, jsonStderr, jsonCode := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "-o", "json", "version")
	if jsonCode != 0 {
		t.Fatalf("json code=%d stderr=%s", jsonCode, jsonStderr)
	}
	if jsonStderr != "" {
		t.Fatalf("json stderr=%q", jsonStderr)
	}
	got := mustJSON(t, jsonStdout)
	if got["api_version"] != "2.0.0-alpha.4" || got["api_build_sha"] != "0123456789abcdef0123456789abcdef01234567" || got["api_built_at"] != "2026-10-02T14:03:11Z" || got["api_url"] != api.srv.URL {
		t.Fatalf("unexpected json: %#v", got)
	}
}

func TestVersion_preservesUnknownServerIdentity(t *testing.T) {
	api := newFakeAPI(t)
	api.on("GET", "/config", 200, versionConfigFixture(api.srv.URL, "0.0.0-dev", "unknown", nil))

	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "-o", "table", "version")
	if code != 0 {
		t.Fatalf("code=%d stderr=%s", code, stderr)
	}
	if stderr != "" {
		t.Fatalf("stderr=%q", stderr)
	}
	if !has(stdout, "Build SHA: unknown") || !has(stdout, "Built At: unknown") {
		t.Fatalf("stdout=%q", stdout)
	}
	if has(stdout, "Warning:") {
		t.Fatalf("stdout unexpectedly contains skew warning: %q", stdout)
	}
}

func TestVersion_degradesWhenConnectedAPIIsUnavailable(t *testing.T) {
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", "http://127.0.0.1:1", "-o", "table", "version")
	if code != 0 {
		t.Fatalf("code=%d stderr=%s", code, stderr)
	}
	if !has(stdout, "daytona ") {
		t.Fatalf("stdout=%q", stdout)
	}
	if !has(stderr, "Connected API unavailable at http://127.0.0.1:1") {
		t.Fatalf("stderr=%q", stderr)
	}

	jsonStdout, jsonStderr, jsonCode := run(t, cfgDir(t), nil, "--api-url", "http://127.0.0.1:1", "-o", "json", "version")
	if jsonCode != 0 {
		t.Fatalf("json code=%d stderr=%s", jsonCode, jsonStderr)
	}
	got := mustJSON(t, jsonStdout)
	if got["api_url"] != "http://127.0.0.1:1" {
		t.Fatalf("unexpected api_url: %#v", got)
	}
	// What could not be read is omitted, not printed empty.
	for _, k := range []string{"api_version", "api_build_sha", "api_built_at", "skew_warning"} {
		if _, ok := got[k]; ok {
			t.Fatalf("unexpected %s: %#v", k, got)
		}
	}
	if !has(jsonStderr, "Connected API unavailable at http://127.0.0.1:1") {
		t.Fatalf("json stderr=%q", jsonStderr)
	}
}

func TestVersion_degradesWhenConnectedAPITimesOut(t *testing.T) {
	prevTimeout := versionFetchTimeout
	versionFetchTimeout = 20 * time.Millisecond
	t.Cleanup(func() { versionFetchTimeout = prevTimeout })

	api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		time.Sleep(200 * time.Millisecond)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"billing_enabled":false,"version":"2.0.0-alpha.4","build_sha":"0123456789abcdef0123456789abcdef01234567","built_at":"2026-10-02T14:03:11Z"}`))
	}))
	defer api.Close()

	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.URL, "-o", "table", "version")
	if code != 0 {
		t.Fatalf("code=%d stderr=%s", code, stderr)
	}
	if !has(stdout, "daytona ") {
		t.Fatalf("stdout=%q", stdout)
	}
	if !has(stderr, "Connected API unavailable at "+api.URL) {
		t.Fatalf("stderr=%q", stderr)
	}
}

func TestVersion_returnsInterruptedExitCodeWhenContextIsCanceled(t *testing.T) {
	api := newFakeAPI(t)
	api.on("GET", "/config", 200, versionConfigFixture(api.srv.URL, "2.0.0-alpha.4", "0123456789abcdef0123456789abcdef01234567", "2026-10-02T14:03:11Z"))

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	root := NewRoot(new(strings.Builder), new(strings.Builder))
	root.SetContext(ctx)
	root.SetArgs([]string{"--api-url", api.srv.URL, "-o", "table", "version"})
	err := root.ExecuteContext(ctx)
	if cerr.ExitCode(err) != cerr.ExitInterrupted {
		t.Fatalf("exit code=%d err=%v", cerr.ExitCode(err), err)
	}
}

// With nothing configured the CLI resolves the default API URL; a canceled
// context must stop before any request reaches it.
func TestVersion_returnsInterruptedExitCodeWhenContextIsCanceledWithDefaultAPI(t *testing.T) {
	t.Setenv("DAYTONA_CONFIG_DIR", cfgDir(t))
	t.Setenv("DAYTONA_API_URL", "")
	t.Setenv("DAYTONA_V2_API_URL", "")
	t.Setenv("CI", "1")

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	root := NewRoot(new(strings.Builder), new(strings.Builder))
	root.SetContext(ctx)
	root.SetArgs([]string{"version"})
	err := root.ExecuteContext(ctx)
	if cerr.ExitCode(err) != cerr.ExitInterrupted {
		t.Fatalf("exit code=%d err=%v", cerr.ExitCode(err), err)
	}
}

func TestVersion_printsSkewWarningWhenAPIVersionIsTooFarAhead(t *testing.T) {
	prevVersion := app.Version
	app.Version = "2.0.0-alpha.1"
	t.Cleanup(func() { app.Version = prevVersion })

	api := newFakeAPI(t)
	api.on("GET", "/config", 200, versionConfigFixture(api.srv.URL, "2.2.0", "0123456789abcdef0123456789abcdef01234567", "2026-10-02T14:03:11Z"))

	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "-o", "table", "version")
	if code != 0 {
		t.Fatalf("code=%d stderr=%s", code, stderr)
	}
	if stderr != "" {
		t.Fatalf("stderr=%q", stderr)
	}
	if !has(stdout, "Warning: CLI version ") || !has(stdout, "differ by more than one minor version") {
		t.Fatalf("stdout=%q", stdout)
	}

	jsonStdout, jsonStderr, jsonCode := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "-o", "json", "version")
	if jsonCode != 0 {
		t.Fatalf("json code=%d stderr=%s", jsonCode, jsonStderr)
	}
	if jsonStderr != "" {
		t.Fatalf("json stderr=%q", jsonStderr)
	}
	got := mustJSON(t, jsonStdout)
	if !strings.Contains(got["skew_warning"].(string), "more than one minor version") {
		t.Fatalf("unexpected json: %#v", got)
	}
}

func TestVersionSkewWarning(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name       string
		cliVersion string
		apiVersion string
		want       string
	}{
		{name: "same minor", cliVersion: "2.3.0", apiVersion: "2.3.9", want: ""},
		{name: "one minor apart", cliVersion: "2.3.0", apiVersion: "2.4.0", want: ""},
		{name: "two minors apart", cliVersion: "2.3.0", apiVersion: "2.5.0", want: "more than one minor"},
		{name: "different major", cliVersion: "2.3.0", apiVersion: "3.0.0", want: "different major"},
		{name: "pre-release ignored", cliVersion: "2.0.0-alpha.3", apiVersion: "2.0.0-alpha.9", want: ""},
		{name: "unknown sentinel ignored", cliVersion: "2.3.0", apiVersion: "0.0.0-dev", want: ""},
		{name: "non semver ignored", cliVersion: "2.3.0", apiVersion: "unknown", want: ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := versionSkewWarning(tt.cliVersion, tt.apiVersion)
			if tt.want == "" {
				if got != "" {
					t.Fatalf("got unexpected warning %q", got)
				}
				return
			}
			if !strings.Contains(got, tt.want) {
				t.Fatalf("warning %q does not contain %q", got, tt.want)
			}
		})
	}
}

// versionConfigFixture is a contract-valid GET /config body carrying the
// given build identity; builtAt nil is the unstamped build's null.
func versionConfigFixture(issuer, version, buildSHA string, builtAt any) map[string]any {
	cfg := oidcConfigFixture(issuer)
	cfg["version"] = version
	cfg["build_sha"] = buildSHA
	cfg["built_at"] = builtAt
	return cfg
}

// A malformed config.yaml must not stop the CLI from reporting its version.
func TestVersion_printsCLIVersionWhenConfigIsMalformed(t *testing.T) {
	d := cfgDir(t)
	if err := os.WriteFile(filepath.Join(d, "config.yaml"), []byte("profiles: [\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	stdout, stderr, code := run(t, d, nil, "-o", "table", "version")
	if code != 0 || !has(stdout, "daytona "+app.Version) || !has(stderr, "config.yaml") {
		t.Fatalf("code=%d stdout=%q stderr=%q", code, stdout, stderr)
	}
	stdout, _, code = run(t, d, nil, "-o", "json", "version")
	if code != 0 || mustJSON(t, stdout)["version"] != app.Version {
		t.Fatalf("json: code=%d stdout=%q", code, stdout)
	}
	if _, _, code := run(t, d, nil, "-o", "bogus", "version"); code != cerr.ExitUsage {
		t.Fatalf("an invalid --output stays a usage error: %d", code)
	}
	if _, _, code := run(t, d, nil, "org", "list"); code == 0 {
		t.Fatal("other commands still fail on a malformed config")
	}
	t.Setenv("DAYTONA_CONFIG_DIR", d)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	root := NewRoot(io.Discard, io.Discard)
	root.SetArgs([]string{"version"})
	if err := root.ExecuteContext(ctx); cerr.ExitCode(err) != cerr.ExitInterrupted {
		t.Fatalf("a cancelled invocation is interrupted, not a version print: %v", err)
	}
}

// --version / -v print the CLI version offline, before config loading.
func TestRootVersionFlags(t *testing.T) {
	d := cfgDir(t)
	if err := os.WriteFile(filepath.Join(d, "config.yaml"), []byte("profiles: [\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, flag := range []string{"--version", "-v"} {
		stdout, stderr, code := run(t, d, nil, flag)
		if code != 0 || stderr != "" || strings.TrimSpace(stdout) != "daytona "+app.Version+" ("+runtime.GOOS+"/"+runtime.GOARCH+")" {
			t.Fatalf("%s: code=%d stdout=%q stderr=%q", flag, code, stdout, stderr)
		}
	}
}
