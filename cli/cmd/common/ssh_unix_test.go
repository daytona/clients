// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build unix

package common

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

// fakeSSH is put first on PATH as "ssh". It records its arguments and the
// config it was given, then exits with FAKE_SSH_EXIT.
const fakeSSH = `#!/bin/sh
printf '%s\n' "$@" > "$FAKE_SSH_RECORD/args"
cat "$2" > "$FAKE_SSH_RECORD/config" || exit 98
exit "${FAKE_SSH_EXIT:-0}"
`

func installFakeSSH(t *testing.T) string {
	t.Helper()
	bin := t.TempDir()
	if err := os.WriteFile(filepath.Join(bin, "ssh"), []byte(fakeSSH), 0o700); err != nil {
		t.Fatal(err)
	}
	record := t.TempDir()
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("FAKE_SSH_RECORD", record)
	return record
}

func checkFakeSSHRecord(t *testing.T, record string) {
	t.Helper()
	args, err := os.ReadFile(filepath.Join(record, "args"))
	if err != nil {
		t.Fatalf("fake ssh did not run: %v", err)
	}
	argv := strings.Split(strings.TrimSuffix(string(args), "\n"), "\n")
	if len(argv) != 3 || argv[0] != "-F" || argv[2] != "ssh.example.com" {
		t.Fatalf("ssh argv = %#v, want [-F <config> ssh.example.com]", argv)
	}
	if slices.ContainsFunc(argv, func(arg string) bool { return strings.Contains(arg, testToken) }) {
		t.Fatalf("ssh argv %#v contains the access token", argv)
	}
	config, err := os.ReadFile(filepath.Join(record, "config"))
	if err != nil || !strings.Contains(string(config), "User "+testToken+"\n") {
		t.Fatalf("ssh config = %q (%v), want it to carry the token as User", config, err)
	}
	if _, err := os.Stat(argv[1]); !errors.Is(err, fs.ErrNotExist) {
		t.Errorf("SSH config %s still present after ExecuteSSH (stat error: %v)", argv[1], err)
	}
}

var executeTarget = SSHTarget{User: testToken, Host: "ssh.example.com", Port: "2222"}

func TestExecuteSSHPassesTokenOnlyThroughConfig(t *testing.T) {
	record := installFakeSSH(t)
	t.Setenv("FAKE_SSH_EXIT", "0")

	if err := ExecuteSSH(executeTarget); err != nil {
		t.Fatalf("ExecuteSSH returned error: %v", err)
	}
	checkFakeSSHRecord(t, record)
}

// A non-zero ssh exit makes ExecuteSSH call os.Exit, which skips deferred
// calls, so this case runs in a child process.
func TestExecuteSSHRemovesConfigBeforeExit(t *testing.T) {
	if os.Getenv("EXECUTE_SSH_CHILD") == "1" {
		// ExecuteSSH exits with ssh's code; returning at all is a failure.
		fmt.Fprintln(os.Stderr, "ExecuteSSH returned:", ExecuteSSH(executeTarget))
		os.Exit(97)
	}

	record := installFakeSSH(t)
	cmd := exec.Command(os.Args[0], "-test.run=^TestExecuteSSHRemovesConfigBeforeExit$")
	cmd.Env = append(os.Environ(), "EXECUTE_SSH_CHILD=1", "FAKE_SSH_EXIT=3")
	out, err := cmd.CombinedOutput()

	var exitErr *exec.ExitError
	if !errors.As(err, &exitErr) || exitErr.ExitCode() != 3 {
		t.Fatalf("child exit = %v, want ssh's exit code 3; output:\n%s", err, out)
	}
	checkFakeSSHRecord(t, record)
}
