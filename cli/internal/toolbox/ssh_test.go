// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"os"
	"runtime"
	"strings"
	"testing"
)

func TestBuildSSHInvocationKeepsCredentialOutOfArgv(t *testing.T) {
	t.Parallel()
	inv, err := buildSSHInvocation(t.TempDir(), SSHTarget{Host: "ssh.example.com", Port: 2222, SandboxID: "sbx", Credential: "tok-secret"}, SSHCommand{Options: []string{"-L", "8080:localhost:80", "-N"}, Args: []string{"uname", "-a"}})
	if err != nil {
		t.Fatal(err)
	}
	defer inv.Cleanup()
	for _, a := range inv.Args {
		if strings.Contains(a, "tok-secret") || strings.Contains(a, "sbx#") {
			t.Fatalf("credential leaked into argv: %v", inv.Args)
		}
	}
	want := []string{"-F", inv.ConfigPath, "-L", "8080:localhost:80", "-N", "daytona-sandbox", "uname", "-a"}
	if len(inv.Args) != len(want) {
		t.Fatalf("args=%v want %v", inv.Args, want)
	}
	for i := range want {
		if inv.Args[i] != want[i] {
			t.Fatalf("arg[%d]=%q want %q", i, inv.Args[i], want[i])
		}
	}
	st, err := os.Stat(inv.ConfigPath)
	if err != nil {
		t.Fatal(err)
	}
	if runtime.GOOS != "windows" && st.Mode().Perm() != 0o600 {
		t.Fatalf("config mode %o", st.Mode().Perm())
	}
	cfg, _ := os.ReadFile(inv.ConfigPath)
	for _, line := range []string{"Host daytona-sandbox", "HostName ssh.example.com", "Port 2222", `User "sbx#tok-secret"`} {
		if !strings.Contains(string(cfg), line) {
			t.Fatalf("config lacks %q:\n%s", line, cfg)
		}
	}
	inv.Cleanup()
	if _, err := os.Stat(inv.ConfigPath); !os.IsNotExist(err) {
		t.Fatal("cleanup must remove the config")
	}
	if _, err := buildSSHInvocation(t.TempDir(), SSHTarget{Host: "h", Port: 22, SandboxID: "sbx", Credential: "tok\nHost evil"}, SSHCommand{}); err == nil {
		t.Fatal("line breaks must be refused (config injection)")
	}
}

func TestValidateSignalName(t *testing.T) {
	t.Parallel()
	if got, err := ValidateSignalName("term"); err != nil || got != "TERM" {
		t.Fatalf("got %q %v", got, err)
	}
	if _, err := ValidateSignalName("SIGTERM"); err == nil {
		t.Fatal("SIG-prefixed signal unexpectedly accepted")
	}
	if _, err := ValidateSignalName("NOPE"); err == nil {
		t.Fatal("unknown signal unexpectedly accepted")
	}
}
