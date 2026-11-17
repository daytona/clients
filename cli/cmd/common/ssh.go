// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package common

import (
	"errors"
	"net"
	"regexp"
	"strconv"
	"strings"
)

const (
	// maxCommandLength bounds the whole response before it is tokenized. The
	// longest command the API can compose is well under this: "ssh -p 65535 "
	// plus a 32 character token plus a host of at most 253 characters.
	maxCommandLength  = 512
	maxHostnameLength = 253
	maxLabelLength    = 63
	minPortNumber     = 1
	maxPortNumber     = 65535
)

var (
	// sshUserPattern matches the user (access token) part of the destination.
	sshUserPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]*$`)
	// sshPortPattern matches a decimal port with no sign, padding or separators.
	sshPortPattern = regexp.MustCompile(`^[0-9]{1,5}$`)
	// sshHostLabelPattern matches a single RFC 1123 hostname label: alphanumeric
	// at both ends, hyphens allowed only in the interior.
	sshHostLabelPattern = regexp.MustCompile(`^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$`)
)

// SSHTarget is a validated SSH destination. User carries the SSH access
// token, so it must never be placed on a command line.
type SSHTarget struct {
	User string
	Host string
	// Port is empty when the API command does not set one.
	Port string
}

// ParseSSHCommand parses the SSH command string returned by the API into its
// validated components.
//
// Only the two formats the API composes are accepted, and they are enforced:
// - "ssh user@host" (port 22)
// - "ssh -p port user@host"
//
// The user, host and port components are validated individually, so no token
// of the original string is passed through. Anything else is rejected with an
// error naming the component at fault; the raw command is never echoed back.
func ParseSSHCommand(sshCommand string) (SSHTarget, error) {
	if len(sshCommand) > maxCommandLength {
		return SSHTarget{}, errors.New("SSH command is too long")
	}

	fields := strings.Fields(sshCommand)
	if len(fields) < 2 {
		return SSHTarget{}, errors.New(`unexpected SSH command format; expected "ssh [-p port] user@host"`)
	}

	if fields[0] != "ssh" {
		return SSHTarget{}, errors.New(`unexpected SSH command format; expected it to start with "ssh"`)
	}

	args := fields[1:]

	port := ""
	if strings.HasPrefix(args[0], "-") {
		if args[0] != "-p" {
			return SSHTarget{}, errors.New("unsupported option in SSH command")
		}
		if len(args) < 2 {
			return SSHTarget{}, errors.New("invalid port in SSH command")
		}
		if err := validateSSHPort(args[1]); err != nil {
			return SSHTarget{}, err
		}
		port = args[1]
		args = args[2:]
	}

	if len(args) == 0 {
		return SSHTarget{}, errors.New(`unexpected SSH command format; expected "ssh [-p port] user@host"`)
	}
	if len(args) > 1 {
		return SSHTarget{}, errors.New("unexpected argument in SSH command")
	}

	destination := args[0]
	if strings.HasPrefix(destination, "-") {
		return SSHTarget{}, errors.New("unsupported option in SSH command")
	}

	user, host, err := splitSSHDestination(destination)
	if err != nil {
		return SSHTarget{}, err
	}

	return SSHTarget{User: user, Host: host, Port: port}, nil
}

// splitSSHDestination splits "user@host" into its validated components.
func splitSSHDestination(destination string) (string, string, error) {
	user, host, found := strings.Cut(destination, "@")
	if !found || strings.Contains(host, "@") {
		return "", "", errors.New(`unexpected SSH command format; expected the destination in the "user@host" form`)
	}

	if !sshUserPattern.MatchString(user) {
		return "", "", errors.New("invalid user in SSH command")
	}

	if err := validateSSHHost(host); err != nil {
		return "", "", err
	}

	return user, host, nil
}

// validateSSHHost accepts a hostname, an IPv4 literal, or a bracketed IPv6
// literal. The API derives the host from a parsed URL, which keeps the
// brackets around IPv6 addresses.
func validateSSHHost(host string) error {
	invalid := errors.New("invalid host in SSH command")

	if host == "" {
		return invalid
	}

	// An IPv6 literal is recognised by its colons rather than by the parsed
	// bytes: IPv4-mapped forms such as "::ffff:c000:201" parse to four bytes yet
	// are written — and emitted by the API — as a bracketed IPv6 host.
	if address, bracketed := strings.CutPrefix(host, "["); bracketed {
		address, closed := strings.CutSuffix(address, "]")
		if !closed || !strings.Contains(address, ":") || net.ParseIP(address) == nil {
			return invalid
		}
		return nil
	}

	if strings.Contains(host, ":") {
		return invalid
	}

	if ip := net.ParseIP(host); ip != nil {
		if ip.To4() == nil {
			return invalid
		}
		return nil
	}

	// A single trailing dot denotes a fully qualified name and is permitted.
	name := strings.TrimSuffix(host, ".")
	if name == "" || len(name) > maxHostnameLength {
		return invalid
	}

	for _, label := range strings.Split(name, ".") {
		if len(label) > maxLabelLength || !sshHostLabelPattern.MatchString(label) {
			return invalid
		}
	}

	return nil
}

func validateSSHPort(port string) error {
	invalid := errors.New("invalid port in SSH command")

	if !sshPortPattern.MatchString(port) {
		return invalid
	}

	number, err := strconv.Atoi(port)
	if err != nil || number < minPortNumber || number > maxPortNumber {
		return invalid
	}

	return nil
}
