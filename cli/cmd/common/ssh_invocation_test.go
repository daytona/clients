// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package common

import (
	"errors"
	"io/fs"
	"os"
	"os/exec"
	"runtime"
	"slices"
	"strings"
	"testing"
)

const testToken = "tokSecret123"

var invocationTests = []struct {
	name       string
	target     SSHTarget
	wantHost   string
	wantConfig string
}{
	{
		name:       "hostname with port",
		target:     SSHTarget{User: testToken, Host: "ssh.example.com", Port: "2222"},
		wantHost:   "ssh.example.com",
		wantConfig: "Host *\n\tUser " + testToken + "\n\tPort 2222\n",
	},
	{
		name:       "hostname without port",
		target:     SSHTarget{User: testToken, Host: "ssh.example.com"},
		wantHost:   "ssh.example.com",
		wantConfig: "Host *\n\tUser " + testToken + "\n",
	},
	{
		name:       "bracketed ipv6 literal with port",
		target:     SSHTarget{User: testToken, Host: "[2001:db8::1]", Port: "2222"},
		wantHost:   "2001:db8::1",
		wantConfig: "Host *\n\tUser " + testToken + "\n\tPort 2222\n",
	},
}

func TestNewSSHInvocationKeepsTokenOffCommandLine(t *testing.T) {
	for _, test := range invocationTests {
		t.Run(test.name, func(t *testing.T) {
			invocation, err := newSSHInvocation(t.TempDir(), test.target)
			if err != nil {
				t.Fatalf("newSSHInvocation returned error: %v", err)
			}
			defer invocation.Cleanup()

			for _, arg := range invocation.Args {
				if strings.Contains(arg, testToken) {
					t.Fatalf("argument %q contains the access token", arg)
				}
			}
			wantArgs := []string{"-F", invocation.ConfigPath, test.wantHost}
			if !slices.Equal(invocation.Args, wantArgs) {
				t.Errorf("Args = %#v, want %#v", invocation.Args, wantArgs)
			}

			config, err := os.ReadFile(invocation.ConfigPath)
			if err != nil {
				t.Fatalf("reading the SSH config: %v", err)
			}
			if string(config) != test.wantConfig {
				t.Errorf("SSH config = %q, want %q", config, test.wantConfig)
			}

			if runtime.GOOS != "windows" {
				info, err := os.Stat(invocation.ConfigPath)
				if err != nil {
					t.Fatalf("stat of the SSH config: %v", err)
				}
				if mode := info.Mode().Perm(); mode != 0o600 {
					t.Errorf("SSH config mode = %o, want 600", mode)
				}
			}

			invocation.Cleanup()
			if _, err := os.Stat(invocation.ConfigPath); !errors.Is(err, fs.ErrNotExist) {
				t.Errorf("SSH config still present after Cleanup (stat error: %v)", err)
			}
			invocation.Cleanup()
		})
	}
}

func TestNewSSHInvocationRejectsUnvalidatedTargets(t *testing.T) {
	tests := []struct {
		name   string
		target SSHTarget
	}{
		{name: "config directive in user", target: SSHTarget{User: "tok\n\tProxyCommand touch /tmp/x", Host: "ssh.example.com"}},
		{name: "empty user", target: SSHTarget{Host: "ssh.example.com"}},
		{name: "whitespace in host", target: SSHTarget{User: testToken, Host: "ssh.example.com\n\tPort 1"}},
		{name: "non numeric port", target: SSHTarget{User: testToken, Host: "ssh.example.com", Port: "22 x"}},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			dir := t.TempDir()
			if _, err := newSSHInvocation(dir, test.target); err == nil {
				t.Fatal("newSSHInvocation accepted an invalid target")
			}
			if entries, _ := os.ReadDir(dir); len(entries) != 0 {
				t.Errorf("newSSHInvocation left %d file(s) behind", len(entries))
			}
		})
	}
}

// TestNewSSHInvocationResolvesWithOpenSSH checks that the local OpenSSH client
// reads the user and port from the generated config. ssh -G prints the
// resolved configuration without connecting, and with -F it reads no other
// configuration file.
func TestNewSSHInvocationResolvesWithOpenSSH(t *testing.T) {
	sshPath, err := exec.LookPath("ssh")
	if err != nil {
		t.Skip("ssh not found in PATH")
	}

	for _, test := range invocationTests {
		t.Run(test.name, func(t *testing.T) {
			invocation, err := newSSHInvocation(t.TempDir(), test.target)
			if err != nil {
				t.Fatalf("newSSHInvocation returned error: %v", err)
			}
			defer invocation.Cleanup()

			out, err := exec.Command(sshPath, append([]string{"-G"}, invocation.Args...)...).Output()
			if err != nil {
				t.Fatalf("ssh -G failed: %v", err)
			}

			resolved := map[string]string{}
			for _, line := range strings.Split(string(out), "\n") {
				if key, value, found := strings.Cut(strings.TrimSpace(line), " "); found {
					if _, seen := resolved[key]; !seen {
						resolved[key] = value
					}
				}
			}

			port := test.target.Port
			if port == "" {
				port = "22"
			}
			want := map[string]string{"user": testToken, "hostname": test.wantHost, "port": port}
			for key, value := range want {
				if resolved[key] != value {
					t.Errorf("ssh -G resolved %s = %q, want %q", key, resolved[key], value)
				}
			}
		})
	}
}
