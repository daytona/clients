// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSSHResolvedConfigUsesCLIHostKeyPolicy(t *testing.T) {
	t.Parallel()
	// Given a config directory requiring OpenSSH quoting.
	name := "config space 'quoted' %h"
	if runtime.GOOS != "windows" {
		name += ` "double" \backslash`
	}
	dir := filepath.Join(t.TempDir(), name)
	inv, err := buildSSHInvocation(dir, SSHTarget{Host: "ssh.example.com", Port: 2222, SandboxID: "sbx", Credential: "test-key"}, SSHCommand{})
	require.NoError(t, err)
	t.Cleanup(inv.Cleanup)
	args := append([]string{"-G"}, inv.Args...)
	// When OpenSSH resolves the actual invocation (without connecting).
	out, err := exec.CommandContext(t.Context(), "ssh", args...).Output()
	// Then the actual gateway, not the shared alias, owns the CLI-only policy.
	require.NoError(t, err)
	values := make(map[string]string)
	for line := range strings.SplitSeq(string(out), "\n") {
		key, value, ok := strings.Cut(line, " ")
		if ok {
			values[key] = value
		}
	}
	require.Equal(t, "ssh.example.com", values["hostname"])
	require.Equal(t, "2222", values["port"])
	require.Equal(t, "sbx#test-key", values["user"])
	require.Equal(t, "accept-new", values["stricthostkeychecking"])
	require.Equal(t, "none", values["globalknownhostsfile"])
	files, err := os.ReadDir(filepath.Join(dir, "ssh"))
	require.NoError(t, err)
	require.Len(t, files, 1)
	require.Equal(t, filepath.Join(dir, "ssh", files[0].Name()), values["userknownhostsfile"])
}

func TestSSHKnownHostsPersistsAcrossSandboxCredentialsAndCleanup(t *testing.T) {
	t.Parallel()
	// Given a pinned gateway key from a prior sandbox session.
	dir := t.TempDir()
	path, err := sshKnownHosts(dir, "ssh.example.com", 2222)
	require.NoError(t, err)
	pinned := []byte("[ssh.example.com]:2222 ssh-ed25519 fixture\n")
	require.NoError(t, os.WriteFile(path, pinned, 0o600))
	// When another sandbox and credential use the same gateway.
	inv, err := buildSSHInvocation(dir, SSHTarget{Host: "ssh.example.com", Port: 2222, SandboxID: "another-sandbox", Credential: "another-key"}, SSHCommand{})
	require.NoError(t, err)
	inv.Cleanup()
	// Then temporary-config cleanup leaves the gateway trust intact.
	got, err := os.ReadFile(path)
	require.NoError(t, err)
	require.Equal(t, pinned, got)
	files, err := filepath.Glob(filepath.Join(dir, "ssh", "known_hosts-*"))
	require.NoError(t, err)
	require.Equal(t, []string{path}, files)
}

func TestSSHKnownHostsScopesTrustByGatewayHostAndPort(t *testing.T) {
	t.Parallel()
	// Given the same config directory and different gateway identities.
	dir := t.TempDir()
	gateways := []struct {
		host string
		port int
	}{
		{"ssh.example.com", 22},
		{"ssh.example.com", 2222},
		{"other.example.com", 22},
		{"2001:db8::1", 22},
	}
	paths := make(map[string]bool)
	for _, gateway := range gateways {
		// When their trust databases are prepared.
		path, err := sshKnownHosts(dir, gateway.host, gateway.port)
		require.NoError(t, err)
		// Then each actual host/port has a distinct owner-only database.
		require.False(t, paths[path])
		paths[path] = true
		st, err := os.Stat(path)
		require.NoError(t, err)
		require.Zero(t, st.Size())
		if runtime.GOOS != "windows" {
			require.Equal(t, os.FileMode(0o600), st.Mode().Perm())
			st, err = os.Stat(filepath.Dir(path))
			require.NoError(t, err)
			require.Equal(t, os.FileMode(0o700), st.Mode().Perm())
		}
	}
}

func TestSSHInvocationRefusesConfigInjection(t *testing.T) {
	t.Parallel()
	for _, field := range []string{"host", "sandbox", "credential"} {
		for _, payload := range []string{"bad\nHost *", "bad\rUser attacker", "bad\x00value", `bad" value`, `bad\`} {
			t.Run(field+"/"+payload, func(t *testing.T) {
				// Given untrusted config data in one interpolated field.
				dir, host, sandbox, credential := t.TempDir(), "ssh.example.com", "sbx", "test-key"
				switch field {
				case "host":
					host = payload
				case "sandbox":
					sandbox = payload
				case "credential":
					credential = payload
				}
				// When it would be serialized into ssh_config.
				_, err := buildSSHInvocation(dir, SSHTarget{Host: host, Port: 22, SandboxID: sandbox, Credential: credential}, SSHCommand{})
				// Then no invocation or credential-bearing diagnostic is returned.
				require.Error(t, err)
				require.NotContains(t, err.Error(), credential)
			})
		}
	}
}

func TestSSHKnownHostsRefusesEnvironmentExpansion(t *testing.T) {
	t.Parallel()
	for _, payload := range []string{"${HOME}", "bad\nHost *", "bad\rUser attacker", "bad\x00path"} {
		t.Run(payload, func(t *testing.T) {
			// Given a path OpenSSH could expand or parse as another config line.
			dir := filepath.Join(t.TempDir(), payload)
			// When preparing the trust database.
			_, err := sshKnownHosts(dir, "ssh.example.com", 22)
			// Then the path cannot redirect trust outside the CLI's directory.
			require.Error(t, err)
		})
	}
}

func TestBuildSSHInvocationUsesConfiguredDirectory(t *testing.T) {
	// Given an isolated configuration environment, without parallel Setenv.
	dir := t.TempDir()
	t.Setenv("DAYTONA_CONFIG_DIR", dir)
	// When the public entrypoint prepares SSH.
	inv, err := BuildSSHInvocation(SSHTarget{Host: "ssh.example.com", Port: 2222, SandboxID: "sbx", Credential: "test-key"}, SSHCommand{})
	require.NoError(t, err)
	t.Cleanup(inv.Cleanup)
	// Then persistent state uses the CLI's configured directory.
	files, err := filepath.Glob(filepath.Join(dir, "ssh", "known_hosts-*"))
	require.NoError(t, err)
	require.Len(t, files, 1)
}
