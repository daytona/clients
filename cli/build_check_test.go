// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

// The release binaries are built by the toolchain go.mod selects, which must
// carry the standard-library security fixes of the maintained 0.x line.
func TestGoMod_requiresAPatchedToolchain(t *testing.T) {
	body, err := os.ReadFile("go.mod")
	require.NoError(t, err)
	m := regexp.MustCompile(`(?m)^go (\d+)\.(\d+)\.(\d+)$`).FindStringSubmatch(string(body))
	require.NotNil(t, m, "go.mod needs a full go X.Y.Z line")
	major, _ := strconv.Atoi(m[1])
	minor, _ := strconv.Atoi(m[2])
	patch, _ := strconv.Atoi(m[3])
	require.True(t, major > 1 || minor > 26 || (minor == 26 && patch >= 10), "go %s.%s.%s is below 1.26.10", m[1], m[2], m[3])
	require.NotContains(t, string(body), "\ntoolchain ", "a toolchain line would let the build pick another compiler than the go line")
}

// buildTiny builds a minimal Go program named name into dir with extra env.
func buildTiny(t *testing.T, dir, name string, flags []string, env ...string) string {
	t.Helper()
	src := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(src, "go.mod"), []byte("module tiny\n\ngo 1.26.10\n"), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(src, "main.go"), []byte("package main\n\nfunc main() {}\n"), 0o644))
	out := filepath.Join(dir, name)
	cmd := exec.Command("go", append(append([]string{"build"}, flags...), "-o", out, ".")...)
	cmd.Dir = src
	cmd.Env = append(os.Environ(), env...)
	msg, err := cmd.CombinedOutput()
	require.NoError(t, err, string(msg))
	return out
}

func TestCheckBinaries_reportsEveryGoodBinaryAfterABadOne(t *testing.T) {
	if _, err := exec.LookPath("bash"); err != nil {
		t.Skip("bash not on PATH")
	}
	dir := t.TempDir()
	bad := buildTiny(t, dir, "daytona-linux-amd64", nil, "CGO_ENABLED=0", "GOOS=linux", "GOARCH=amd64")
	good := buildTiny(t, dir, "daytona-linux-arm64", []string{"-trimpath"}, "CGO_ENABLED=0", "GOOS=linux", "GOARCH=arm64")

	out, err := exec.Command("bash", "hack/check-binaries.sh", "1.26.10", bad, good).CombinedOutput()

	require.Error(t, err, string(out))
	require.Contains(t, string(out), bad+" lacks 'build -trimpath=true'")
	require.Contains(t, string(out), good+": go")
	require.NotContains(t, string(out), bad+": go")
}

func TestCheckBinaries_readsTheBuildInformation(t *testing.T) {
	if _, err := exec.LookPath("bash"); err != nil {
		t.Skip("bash not on PATH")
	}
	dir := t.TempDir()
	static := []string{"CGO_ENABLED=0", "GOOS=linux", "GOARCH=arm64"}
	good := buildTiny(t, dir, "daytona-linux-arm64", []string{"-trimpath"}, static...)
	untrimmed := buildTiny(t, dir, "daytona-linux-amd64", nil, "CGO_ENABLED=0", "GOOS=linux", "GOARCH=amd64")
	misnamed := buildTiny(t, dir, "daytona-darwin-arm64", []string{"-trimpath"}, static...)
	// The floors below the build are fixed (go.mod requires 1.26.10); the ones
	// above it follow the toolchain that ran this test.
	goVersion, err := exec.Command("go", "env", "GOVERSION").Output()
	require.NoError(t, err)
	m := regexp.MustCompile(`^go(\d+)\.(\d+)(?:\.(\d+))?`).FindStringSubmatch(strings.TrimSpace(string(goVersion)))
	require.NotNil(t, m, "unexpected GOVERSION %q", goVersion)
	major, _ := strconv.Atoi(m[1])
	minor, _ := strconv.Atoi(m[2])
	patch, _ := strconv.Atoi(m[3])
	built := fmt.Sprintf("%d.%d.%d", major, minor, patch)
	nextPatch := fmt.Sprintf("%d.%d.%d", major, minor, patch+1)
	nextMinor := fmt.Sprintf("%d.%d.0", major, minor+1)
	notGo := filepath.Join(dir, "daytona-windows-amd64.exe")
	require.NoError(t, os.WriteFile(notGo, []byte("MZ"), 0o755))

	for _, tc := range []struct {
		name, min, bin, message string
	}{
		{name: "patched static build", min: "1.26.10", bin: good},
		{name: "the toolchain's own version", min: built, bin: good},
		{name: "numeric, not lexical, comparison", min: "1.26.9", bin: good},
		{name: "a floor without a patch number", min: "1.26", bin: good},
		{name: "toolchain below the floor", min: nextPatch, bin: good, message: "below the required go" + nextPatch},
		{name: "floor on a higher minor", min: nextMinor, bin: good, message: "below the required go" + nextMinor},
		{name: "without -trimpath", min: "1.26.10", bin: untrimmed, message: "lacks 'build -trimpath=true'"},
		{name: "another OS than its name", min: "1.26.10", bin: misnamed, message: "lacks 'build GOOS=darwin'"},
		{name: "not a Go binary", min: "1.26.10", bin: notGo, message: "not a Go binary"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			out, err := exec.Command("bash", "hack/check-binaries.sh", tc.min, tc.bin).CombinedOutput()

			if tc.message == "" {
				require.NoError(t, err, string(out))
				require.True(t, strings.HasSuffix(strings.TrimSpace(string(out)), "linux/arm64"), string(out))
				return
			}
			require.Error(t, err, string(out))
			require.Contains(t, string(out), tc.message)
		})
	}
}
