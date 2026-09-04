// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package common

import (
	"slices"
	"strings"
	"testing"
)

func TestParseSSHCommandAcceptsDocumentedFormats(t *testing.T) {
	tests := []struct {
		name    string
		command string
		want    []string
	}{
		{
			name:    "hostname without port",
			command: "ssh tok123@example.com",
			want:    []string{"tok123@example.com"},
		},
		{
			name:    "hostname with port",
			command: "ssh -p 2222 tok123@example.com",
			want:    []string{"-p", "2222", "tok123@example.com"},
		},
		{
			name:    "ipv4 literal",
			command: "ssh tok123@192.0.2.1",
			want:    []string{"tok123@192.0.2.1"},
		},
		{
			name:    "bracketed ipv6 literal with port",
			command: "ssh -p 2222 tok123@[2001:db8::1]",
			want:    []string{"-p", "2222", "tok123@[2001:db8::1]"},
		},
		{
			name:    "bracketed ipv4 mapped ipv6 literal",
			command: "ssh tok123@[::ffff:c000:201]",
			want:    []string{"tok123@[::ffff:c000:201]"},
		},
		{
			name:    "bracketed ipv4 mapped ipv6 literal in dotted form",
			command: "ssh tok123@[::ffff:192.0.2.1]",
			want:    []string{"tok123@[::ffff:192.0.2.1]"},
		},
		{
			name:    "fully qualified hostname with trailing dot",
			command: "ssh tok123@sub.domain.example.com.",
			want:    []string{"tok123@sub.domain.example.com."},
		},
		{
			name:    "irregular internal whitespace",
			command: "ssh   -p   2222   tok123@example.com",
			want:    []string{"-p", "2222", "tok123@example.com"},
		},
		{
			name:    "lowest valid port",
			command: "ssh -p 1 tok123@example.com",
			want:    []string{"-p", "1", "tok123@example.com"},
		},
		{
			name:    "highest valid port",
			command: "ssh -p 65535 tok123@example.com",
			want:    []string{"-p", "65535", "tok123@example.com"},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got, err := ParseSSHCommand(test.command)
			if err != nil {
				t.Fatalf("ParseSSHCommand(%q) returned error: %v", test.command, err)
			}
			if !slices.Equal(got, test.want) {
				t.Errorf("ParseSSHCommand(%q) = %#v, want %#v", test.command, got, test.want)
			}
		})
	}
}

func TestParseSSHCommandRejectsUnsupportedInput(t *testing.T) {
	tests := []struct {
		name    string
		command string
		wantErr string
	}{
		{
			name:    "trailing extra argument",
			command: "ssh tok123@example.com extra",
			wantErr: "unexpected argument in SSH command",
		},
		{
			name:    "destination starting with a dash",
			command: "ssh -p 2222 -tok123@example.com",
			wantErr: "unsupported option in SSH command",
		},
		{
			name:    "attached option with a value",
			command: "ssh -oSomething=value tok123@example.com",
			wantErr: "unsupported option in SSH command",
		},
		{
			name:    "separate option with a value",
			command: "ssh -o Something=value tok123@example.com",
			wantErr: "unsupported option in SSH command",
		},
		{
			name:    "double dash terminator",
			command: "ssh -- tok123@example.com",
			wantErr: "unsupported option in SSH command",
		},
		{
			name:    "attached port form",
			command: "ssh -p2222 tok123@example.com",
			wantErr: "unsupported option in SSH command",
		},
		{
			name:    "non numeric port",
			command: "ssh -p port tok123@example.com",
			wantErr: "invalid port in SSH command",
		},
		{
			name:    "port zero",
			command: "ssh -p 0 tok123@example.com",
			wantErr: "invalid port in SSH command",
		},
		{
			name:    "port above the valid range",
			command: "ssh -p 70000 tok123@example.com",
			wantErr: "invalid port in SSH command",
		},
		{
			name:    "missing port value",
			command: "ssh -p",
			wantErr: "invalid port in SSH command",
		},
		{
			name:    "host with an empty label",
			command: "ssh tok123@a..b",
			wantErr: "invalid host in SSH command",
		},
		{
			name:    "host with a hyphen leading label",
			command: "ssh tok123@a.-b",
			wantErr: "invalid host in SSH command",
		},
		{
			name:    "host with a hyphen trailing label",
			command: "ssh tok123@a-.b",
			wantErr: "invalid host in SSH command",
		},
		{
			name:    "label above the length limit",
			command: "ssh tok123@" + strings.Repeat("a", 64) + ".example.com",
			wantErr: "invalid host in SSH command",
		},
		{
			name:    "unbracketed ipv6 host",
			command: "ssh tok123@2001:db8::1",
			wantErr: "invalid host in SSH command",
		},
		{
			name:    "unbracketed ipv4 mapped ipv6 host",
			command: "ssh tok123@::ffff:c000:201",
			wantErr: "invalid host in SSH command",
		},
		{
			name:    "unclosed bracketed host",
			command: "ssh tok123@[2001:db8::1",
			wantErr: "invalid host in SSH command",
		},
		{
			name:    "bracketed ipv4 host",
			command: "ssh tok123@[192.0.2.1]",
			wantErr: "invalid host in SSH command",
		},
		{
			name:    "bracketed hostname",
			command: "ssh tok123@[example.com]",
			wantErr: "invalid host in SSH command",
		},
		{
			name:    "empty host",
			command: "ssh tok123@",
			wantErr: "invalid host in SSH command",
		},
		{
			name:    "missing separator",
			command: "ssh tok123example.com",
			wantErr: `expected the destination in the "user@host" form`,
		},
		{
			name:    "two separators",
			command: "ssh tok123@example.com@example.net",
			wantErr: `expected the destination in the "user@host" form`,
		},
		{
			name:    "empty user",
			command: "ssh @example.com",
			wantErr: "invalid user in SSH command",
		},
		{
			name:    "user starting with a dot",
			command: "ssh .tok123@example.com",
			wantErr: "invalid user in SSH command",
		},
		{
			name:    "first token is not ssh",
			command: "notssh tok123@example.com",
			wantErr: `expected it to start with "ssh"`,
		},
		{
			name:    "fewer than two tokens",
			command: "ssh",
			wantErr: `expected "ssh [-p port] user@host"`,
		},
		{
			name:    "empty command",
			command: "",
			wantErr: `expected "ssh [-p port] user@host"`,
		},
		{
			name:    "port without a destination",
			command: "ssh -p 2222",
			wantErr: `expected "ssh [-p port] user@host"`,
		},
		{
			name:    "command above the length limit",
			command: "ssh " + strings.Repeat("t", 600) + "@example.com",
			wantErr: "SSH command is too long",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got, err := ParseSSHCommand(test.command)
			if err == nil {
				t.Fatalf("ParseSSHCommand(%q) = %#v, want an error", test.command, got)
			}
			if got != nil {
				t.Errorf("ParseSSHCommand(%q) returned %#v alongside the error, want nil", test.command, got)
			}
			if !strings.Contains(err.Error(), test.wantErr) {
				t.Errorf("ParseSSHCommand(%q) error = %q, want it to contain %q", test.command, err, test.wantErr)
			}
		})
	}
}
