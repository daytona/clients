// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package common

import (
	"slices"
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
	}{
		{
			name:    "trailing extra argument",
			command: "ssh tok123@example.com extra",
		},
		{
			name:    "destination starting with a dash",
			command: "ssh -p 2222 -tok123@example.com",
		},
		{
			name:    "attached option with a value",
			command: "ssh -oSomething=value tok123@example.com",
		},
		{
			name:    "separate option with a value",
			command: "ssh -o Something=value tok123@example.com",
		},
		{
			name:    "double dash terminator",
			command: "ssh -- tok123@example.com",
		},
		{
			name:    "attached port form",
			command: "ssh -p2222 tok123@example.com",
		},
		{
			name:    "non numeric port",
			command: "ssh -p port tok123@example.com",
		},
		{
			name:    "port zero",
			command: "ssh -p 0 tok123@example.com",
		},
		{
			name:    "port above the valid range",
			command: "ssh -p 70000 tok123@example.com",
		},
		{
			name:    "missing port value",
			command: "ssh -p",
		},
		{
			name:    "host with an empty label",
			command: "ssh tok123@a..b",
		},
		{
			name:    "host with a hyphen leading label",
			command: "ssh tok123@a.-b",
		},
		{
			name:    "host with a hyphen trailing label",
			command: "ssh tok123@a-.b",
		},
		{
			name:    "unbracketed ipv6 host",
			command: "ssh tok123@2001:db8::1",
		},
		{
			name:    "unclosed bracketed host",
			command: "ssh tok123@[2001:db8::1",
		},
		{
			name:    "bracketed ipv4 host",
			command: "ssh tok123@[192.0.2.1]",
		},
		{
			name:    "missing separator",
			command: "ssh tok123example.com",
		},
		{
			name:    "two separators",
			command: "ssh tok123@example.com@example.net",
		},
		{
			name:    "empty user",
			command: "ssh @example.com",
		},
		{
			name:    "empty host",
			command: "ssh tok123@",
		},
		{
			name:    "user starting with a dot",
			command: "ssh .tok123@example.com",
		},
		{
			name:    "first token is not ssh",
			command: "notssh tok123@example.com",
		},
		{
			name:    "fewer than two tokens",
			command: "ssh",
		},
		{
			name:    "empty command",
			command: "",
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
		})
	}
}
