// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"os"
	"testing"
)

// sshSessionEnv are the variables `daytona login` reads to detect an SSH
// session (isSSHSession), which switches it to the device flow.
var sshSessionEnv = []string{"SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"}

// TestMain clears the SSH session variables the suite would otherwise inherit
// from the shell running it, so a test sees an SSH session only when it sets
// one with t.Setenv.
func TestMain(m *testing.M) {
	for _, key := range sshSessionEnv {
		_ = os.Unsetenv(key)
	}
	os.Exit(m.Run())
}

func TestSuiteDoesNotInheritAnSSHSession(t *testing.T) {
	for _, key := range sshSessionEnv {
		if v, ok := os.LookupEnv(key); ok {
			t.Errorf("%s=%q is set: login tests would take the device flow", key, v)
		}
	}
}
