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

// Windows PowerShell 5.1 parses web responses with the Internet Explorer
// engine unless -UseBasicParsing is passed, and fails where that engine is
// absent. pwsh ignores the switch, so the pwsh tests cannot catch a missing one.
func TestInstallPs1_passesUseBasicParsingToEveryWebCall(t *testing.T) {
	body, err := os.ReadFile("install.ps1")
	require.NoError(t, err)
	calls := 0
	for line := range strings.Lines(string(body)) {
		code := strings.TrimSpace(line)
		if strings.HasPrefix(code, "#") || !(strings.Contains(code, "Invoke-RestMethod ") || strings.Contains(code, "Invoke-WebRequest ")) {
			continue
		}
		calls++
		require.Contains(t, code, "-UseBasicParsing", "web call without -UseBasicParsing: %s", code)
	}
	require.Equal(t, 3, calls, "the release list and the two downloads")
	require.NotContains(t, string(body), "/releases/latest", "the installer ranks releases itself")
}

func TestGenScript_prefersGOBINWithGOPATHFallback(t *testing.T) {
	body, err := os.ReadFile("hack/gen.sh")
	require.NoError(t, err)
	script := string(body)
	require.Contains(t, script, "go env GOBIN")
	require.Contains(t, script, "$(go env GOPATH)/bin/oapi-codegen")
}

func writeMockTool(t *testing.T, path string, body string) {
	t.Helper()
	require.NoError(t, os.WriteFile(path, []byte(body), 0o755))
}
