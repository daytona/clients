// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package main

import (
	"crypto/sha256"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
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

// installShWithAsset runs install.sh for a pinned version against a mock curl
// serving `asset` as the release binary, into binDir.
func installShWithAsset(t *testing.T, binDir string, asset string) (string, error) {
	t.Helper()
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("sh not on PATH")
	}
	mockDir := filepath.Join(t.TempDir(), "mock")
	require.NoError(t, os.MkdirAll(mockDir, 0o755))
	assetFile := filepath.Join(t.TempDir(), "asset")
	require.NoError(t, os.WriteFile(assetFile, []byte(asset), 0o644))
	writeMockTool(t, filepath.Join(mockDir, "uname"), `#!/bin/sh
if [ "${1:-}" = "-s" ]; then printf 'Linux\n'; else printf 'x86_64\n'; fi
`)
	writeMockTool(t, filepath.Join(mockDir, "curl"), fmt.Sprintf(`#!/bin/sh
set -eu
out=''
url=''
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
case "$url" in
  */v2.0.0-alpha.9/daytona-linux-amd64) cp '%s' "$out" ;;
  */v2.0.0-alpha.9/SHA256SUMS) printf '%%s  daytona-linux-amd64\n' '%x' > "$out" ;;
  *) echo "unexpected url: $url" >&2; exit 1 ;;
esac
`, assetFile, sha256.Sum256([]byte(asset))))
	cmd := exec.Command("sh", "install.sh", "--version", "2.0.0-alpha.9", "--dir", binDir)
	cmd.Env = append(os.Environ(), "PATH="+mockDir+":"+os.Getenv("PATH"))
	out, err := cmd.CombinedOutput()
	return string(out), err
}

func requireNoStagedBinary(t *testing.T, dir string) {
	t.Helper()
	entries, err := os.ReadDir(dir)
	require.NoError(t, err)
	for _, entry := range entries {
		require.False(t, strings.HasPrefix(entry.Name(), ".daytona.new."), "staged binary left behind: %s", entry.Name())
	}
}

func TestInstallSh_keepsInstalledBinaryWhenTheNewOneFailsToRun(t *testing.T) {
	binDir := t.TempDir()
	installed := filepath.Join(binDir, "daytona")
	require.NoError(t, os.WriteFile(installed, []byte("#!/bin/sh\necho old\n"), 0o755))

	out, err := installShWithAsset(t, binDir, "#!/bin/sh\nexit 3\n")

	require.Error(t, err, out)
	require.Contains(t, out, "failed to run")
	body, readErr := os.ReadFile(installed)
	require.NoError(t, readErr)
	require.Equal(t, "#!/bin/sh\necho old\n", string(body), "the working binary is left in place")
	requireNoStagedBinary(t, binDir)
}

func TestInstallSh_refusesADirectoryAtTheTarget(t *testing.T) {
	binDir := t.TempDir()
	require.NoError(t, os.Mkdir(filepath.Join(binDir, "daytona"), 0o755))

	out, err := installShWithAsset(t, binDir, "#!/bin/sh\necho new\n")

	require.Error(t, err, out)
	require.Contains(t, out, "is a directory")
	entries, readErr := os.ReadDir(filepath.Join(binDir, "daytona"))
	require.NoError(t, readErr)
	require.Empty(t, entries)
	requireNoStagedBinary(t, binDir)
}

func TestInstallSh_replacesInstalledBinaryAfterTheCheck(t *testing.T) {
	binDir := t.TempDir()
	installed := filepath.Join(binDir, "daytona")
	require.NoError(t, os.WriteFile(installed, []byte("#!/bin/sh\necho old\n"), 0o755))
	asset := "#!/bin/sh\necho \"new $*\"\n"

	out, err := installShWithAsset(t, binDir, asset)

	require.NoError(t, err, out)
	require.Contains(t, out, "new version")
	body, readErr := os.ReadFile(installed)
	require.NoError(t, readErr)
	require.Equal(t, asset, string(body))
	requireNoStagedBinary(t, binDir)
}

// installPs1 drives install.ps1 under pwsh the way `irm | iex` does, with
// Invoke-WebRequest shadowed to serve `asset`, and reports the caller's
// $ErrorActionPreference afterwards.
func installPs1(t *testing.T, installDir, asset string) (string, error) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("the fake release binary is a shell script")
	}
	pwsh, err := exec.LookPath("pwsh")
	if err != nil {
		t.Skip("pwsh not on PATH")
	}
	assets := t.TempDir()
	assetName := "daytona-windows-amd64.exe"
	require.NoError(t, os.WriteFile(filepath.Join(assets, assetName), []byte(asset), 0o755))
	require.NoError(t, os.WriteFile(filepath.Join(assets, "SHA256SUMS"), []byte(fmt.Sprintf("%x  %s\n", sha256.Sum256([]byte(asset)), assetName)), 0o644))
	driver := `$ErrorActionPreference = "Continue"
function Invoke-WebRequest {
  param([Parameter(Position = 0)] $Uri, [switch] $UseBasicParsing, $OutFile)
  $name = Split-Path $Uri -Leaf
  Copy-Item (Join-Path $env:MOCK_ASSETS $name) $OutFile
  if ($name -like "*.exe") { & chmod +x $OutFile }
}
try { Get-Content -Raw $env:INSTALL_PS1 | Invoke-Expression } catch { Write-Host "INSTALL_FAILED: $_" }
Write-Host "CALLER_EAP=$ErrorActionPreference"
`
	script, err := filepath.Abs("install.ps1")
	require.NoError(t, err)
	cmd := exec.Command(pwsh, "-NoProfile", "-NonInteractive", "-Command", driver)
	cmd.Env = append(os.Environ(),
		"INSTALL_PS1="+script,
		"MOCK_ASSETS="+assets,
		"DAYTONA_VERSION=v2.0.0-alpha.9",
		"DAYTONA_INSTALL_DIR="+installDir,
		"PROCESSOR_ARCHITECTURE=AMD64",
		"PROCESSOR_ARCHITEW6432=",
		"TEMP="+t.TempDir(),
	)
	out, err := cmd.CombinedOutput()
	return string(out), err
}

func TestInstallPs1_keepsInstalledBinaryWhenTheNewOneFailsToRun(t *testing.T) {
	installDir := t.TempDir()
	installed := filepath.Join(installDir, "daytona.exe")
	require.NoError(t, os.WriteFile(installed, []byte("old"), 0o755))

	out, err := installPs1(t, installDir, "#!/bin/sh\nexit 3\n")

	require.NoError(t, err, out)
	require.Contains(t, out, "INSTALL_FAILED")
	require.Contains(t, out, "failed verification with exit code 3")
	require.Contains(t, out, "CALLER_EAP=Continue", "the caller's preference is untouched")
	body, readErr := os.ReadFile(installed)
	require.NoError(t, readErr)
	require.Equal(t, "old", string(body), "the working binary is left in place")
	requireNoStagedBinary(t, installDir)
}

// A first install (no daytona.exe yet) into a directory whose name holds
// PowerShell wildcard characters.
func TestInstallPs1_freshInstallIntoWildcardDirectory(t *testing.T) {
	installDir := filepath.Join(t.TempDir(), "bin[1]")
	asset := "#!/bin/sh\necho \"new $*\"\n"

	out, err := installPs1(t, installDir, asset)

	require.NoError(t, err, out)
	require.NotContains(t, out, "INSTALL_FAILED")
	body, readErr := os.ReadFile(filepath.Join(installDir, "daytona.exe"))
	require.NoError(t, readErr)
	require.Equal(t, asset, string(body))
	requireNoStagedBinary(t, installDir)
}

func TestInstallPs1_replacesInstalledBinaryAfterTheCheck(t *testing.T) {
	installDir := t.TempDir()
	installed := filepath.Join(installDir, "daytona.exe")
	require.NoError(t, os.WriteFile(installed, []byte("old"), 0o755))
	asset := "#!/bin/sh\necho \"new $*\"\n"

	out, err := installPs1(t, installDir, asset)

	require.NoError(t, err, out)
	require.NotContains(t, out, "INSTALL_FAILED")
	require.Contains(t, out, "new version")
	require.Contains(t, out, "CALLER_EAP=Continue", "the caller's preference is untouched")
	body, readErr := os.ReadFile(installed)
	require.NoError(t, readErr)
	require.Equal(t, asset, string(body))
	requireNoStagedBinary(t, installDir)
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
