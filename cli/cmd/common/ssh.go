// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package common

import (
	"fmt"
	"regexp"
	"strings"
)

// sshCommandPattern matches the only two command shapes the API emits.
//
// The character classes must never be widened to admit whitespace. ParseSSHCommand
// relies on a matching string containing exactly the literal spaces written below,
// so that strings.Fields cannot yield an extra token for ssh(1) to read as an
// option.
//
// The token is opaque to the CLI, so the only constraint that matters is the first
// character: the destination is rendered as "token@host", and a token starting with
// '-' would make the whole argument read as an ssh(1) option. The rest of the
// NanoID URL alphabet is accepted rather than pinned to the alphabet the API
// happens to use today, so a change there cannot break already-released clients.
//
// \A and \z rather than ^ and $ so the end-of-text guarantee is local to the
// pattern and survives a later (?m) or a port to a PCRE-family engine, where $
// also matches before a trailing newline.
var sshCommandPattern = regexp.MustCompile(
	`\Assh (?:-p [0-9]{1,5} )?[A-Za-z0-9_][A-Za-z0-9_-]*@(?:\[[0-9A-Fa-f:.]{2,45}\]|[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.?)\z`,
)

// ParseSSHCommand parses the SSH command string returned by the API.
//
// The server is not trusted to supply an argument vector. The command is matched
// against the only two shapes the API emits and anything else is rejected, so a
// malicious or compromised API server cannot inject ssh(1) options such as
// -o ProxyCommand, which ssh(1) executes through the local shell (CWE-88).
//
// Expected formats:
// - "ssh token@host" (port 22)
// - "ssh -p port token@host"
func ParseSSHCommand(sshCommand string) ([]string, error) {
	// The pattern bounds the host but not the token, so total length is bounded
	// here rather than by the pattern. The purpose is to cap the cost of scanning a
	// hostile multi-megabyte response; 512 bytes leaves roughly 15x headroom over
	// the longest command any server emits, which carries a 32 character token.
	//
	// The error deliberately omits the server-supplied string: it is unbounded and
	// carries the SSH access token, which would end up in scrollback and CI logs.
	if len(sshCommand) > 512 || !sshCommandPattern.MatchString(sshCommand) {
		return nil, fmt.Errorf("unexpected SSH command format from server")
	}

	// Safe because the pattern admits no whitespace beyond its own literal spaces.
	return strings.Fields(sshCommand)[1:], nil
}
