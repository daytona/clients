// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package main

import (
	"crypto/sha256"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestInstallSh_usesNewestReleaseEndpoint(t *testing.T) {
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("sh not on PATH")
	}
	binDir := filepath.Join(t.TempDir(), "bin")
	mockDir := filepath.Join(t.TempDir(), "mock")
	require.NoError(t, os.MkdirAll(mockDir, 0o755))
	logFile := filepath.Join(t.TempDir(), "curl.log")
	asset := []byte(`#!/bin/sh
exit 0
`)
	assetSum := fmt.Sprintf("%x", sha256.Sum256(asset))
	writeMockTool(t, filepath.Join(mockDir, "uname"), `#!/bin/sh
if [ "${1:-}" = "-s" ]; then
  printf 'Linux\n'
else
  printf 'x86_64\n'
fi
`)
	writeMockTool(t, filepath.Join(mockDir, "curl"), fmt.Sprintf(`#!/bin/sh
set -eu
log="${MOCK_CURL_LOG:?}"
out=''
url=''
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
printf '%%s\n' "$url" >> "$log"
case "$url" in
  *api.github.com/repos/daytona/clients/releases?per_page=100)
    # Newest first, like GitHub: a V0 stable release and an SDK tag sit
    # ABOVE the newest CLI alpha — the installer must skip them.
    printf '[{"tag_name":"v0.192.1"},{"tag_name":"python-sdk-1.4.0"},{"tag_name":"v2.0.0-alpha.9"},{"tag_name":"v2.0.0-alpha.8"}]'
    ;;
  *github.com/daytona/clients/releases/download/v2.0.0-alpha.9/daytona-linux-amd64)
	    printf '#!/bin/sh\nexit 0\n' > "$out"
    chmod +x "$out"
    ;;
  *github.com/daytona/clients/releases/download/v2.0.0-alpha.9/SHA256SUMS)
    printf '%%s  daytona-linux-amd64\n' '%s' > "$out"
    ;;
  *)
    echo "unexpected url: $url" >&2
    exit 1
    ;;
esac
`, assetSum))

	cmd := exec.Command("sh", "install.sh")
	cmd.Dir = "."
	cmd.Env = append(os.Environ(),
		"DAYTONA_INSTALL_DIR="+binDir,
		"DAYTONA_RELEASE_REPO=daytona/clients",
		"MOCK_CURL_LOG="+logFile,
		"PATH="+mockDir+":"+os.Getenv("PATH"),
	)
	out, err := cmd.CombinedOutput()
	require.NoError(t, err, string(out))
	_, err = os.Stat(filepath.Join(binDir, "daytona"))
	require.NoError(t, err)
	logged, err := os.ReadFile(logFile)
	require.NoError(t, err)
	require.Contains(t, string(logged), "/releases?per_page=100")
	require.NotContains(t, string(logged), "/releases/latest")
	require.Contains(t, string(logged), "/v2.0.0-alpha.9/", "the newest v2.* tag, not the newest release of any kind")
}

func TestInstallSh_errorsWhenNoReleaseExists(t *testing.T) {
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("sh not on PATH")
	}
	mockDir := filepath.Join(t.TempDir(), "mock")
	require.NoError(t, os.MkdirAll(mockDir, 0o755))
	writeMockTool(t, filepath.Join(mockDir, "uname"), `#!/bin/sh
if [ "${1:-}" = "-s" ]; then
  printf 'Linux\n'
else
  printf 'x86_64\n'
fi
`)
	writeMockTool(t, filepath.Join(mockDir, "curl"), `#!/bin/sh
set -eu
printf '[]'
`)
	cmd := exec.Command("sh", "install.sh")
	cmd.Dir = "."
	cmd.Env = append(os.Environ(), "PATH="+mockDir+":"+os.Getenv("PATH"))
	out, err := cmd.CombinedOutput()
	require.Error(t, err)
	require.Contains(t, string(out), "could not determine the newest 2.x CLI release")
}

func TestInstallPs1_usesPrereleaseLookupAndBasicParsing(t *testing.T) {
	body, err := os.ReadFile("install.ps1")
	require.NoError(t, err)
	script := string(body)
	require.NotContains(t, script, "/releases/latest")
	require.Contains(t, script, "https://api.github.com/repos/$Repo/releases")
	require.Contains(t, script, "@(Invoke-RestMethod")
	require.Contains(t, script, `-like "v2.*"`, "picks the CLI's own 2.x line, not the newest release of any kind")
	require.Equal(t, 3, strings.Count(script, "-UseBasicParsing"))
	require.Contains(t, script, "$LASTEXITCODE -ne 0")
}

func TestGenScript_prefersGOBINWithGOPATHFallback(t *testing.T) {
	body, err := os.ReadFile("hack/gen.sh")
	require.NoError(t, err)
	script := string(body)
	require.Contains(t, script, "go env GOBIN")
	require.Contains(t, script, "$(go env GOPATH)/bin/oapi-codegen")
}

func TestUpdateShaScript_usesSafeArgCheckAndAnchoredAlphaGuard(t *testing.T) {
	body, err := os.ReadFile("hack/homebrew/update_sha.sh")
	require.NoError(t, err)
	script := string(body)
	require.Contains(t, script, "${1:-}")
	require.Contains(t, script, `^[0-9]+\.[0-9]+\.[0-9]+-alpha\.[0-9]+$`)
}

func writeMockTool(t *testing.T, path string, body string) {
	t.Helper()
	require.NoError(t, os.WriteFile(path, []byte(body), 0o755))
}
