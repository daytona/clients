// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package sandbox

import (
	"os"
	"os/exec"
	"strings"
	"testing"
)

func TestBuildCommandSingleArgumentIsVerbatim(t *testing.T) {
	cases := []string{
		"ls",
		"ls | grep foo",
		"echo $HOME && ls *.txt",
		"python3 -c \"print('hi there')\"",
	}

	for _, arg := range cases {
		if got := buildCommand([]string{arg}); got != arg {
			t.Errorf("buildCommand([%q]) = %q, want verbatim", arg, got)
		}
	}
}

// roundTripArgv runs the built command through /bin/sh and returns the argv
// the shell actually produced, by prepending a printf that echoes each
// argument on its own line.
func roundTripArgv(t *testing.T, args []string) []string {
	t.Helper()

	command := buildCommand(append([]string{"printf", "%s\\n"}, args...))
	out, err := exec.Command("/bin/sh", "-c", command).Output()
	if err != nil {
		t.Fatalf("sh -c %q failed: %v", command, err)
	}
	return strings.Split(strings.TrimSuffix(string(out), "\n"), "\n")
}

func TestBuildCommandPreservesArgumentBoundaries(t *testing.T) {
	if _, err := os.Stat("/bin/sh"); err != nil {
		t.Skip("/bin/sh not available")
	}

	cases := [][]string{
		{"python3", "-c", "print('hi there')"},
		{"ls", "my file.txt"},
		{"echo", "$HOME"},
		{"echo", "a && b", "|", ">out", "*.txt"},
		{"printf", "%s", "double \" and single ' quotes"},
		{"grep", "-e", "^foo.*bar$", "file with  double  spaces"},
		{"echo", ""},
		{"echo", "back\\slash", "tab\there"},
	}

	for _, args := range cases {
		got := roundTripArgv(t, args)
		if len(got) != len(args) {
			t.Errorf("argv %q: shell produced %d args %q, want %d", args, len(got), got, len(args))
			continue
		}
		for i := range args {
			if got[i] != args[i] {
				t.Errorf("argv %q: arg %d = %q, want %q", args, i, got[i], args[i])
			}
		}
	}
}
