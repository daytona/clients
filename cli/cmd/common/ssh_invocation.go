// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package common

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"strings"
)

// SSHInvocation is the argument vector for ssh plus the temporary ssh_config
// it reads. Call Cleanup once ssh has exited.
type SSHInvocation struct {
	Args       []string
	ConfigPath string
}

// NewSSHInvocation prepares an ssh call that keeps the access token off the
// command line. The token is the SSH username, and as "user@host" in the
// arguments it would be readable by every local user through the process list
// for the whole session. Instead it is written as User in a temporary
// ssh_config, readable only by the current user, which ssh loads with -F.
//
// With -F, ssh reads no other configuration file: the user's and the system
// ssh_config do not apply to this connection, so none of their options can
// change the user, host or port, or pass the username to another process.
func NewSSHInvocation(target SSHTarget) (SSHInvocation, error) {
	return newSSHInvocation("", target)
}

func newSSHInvocation(dir string, target SSHTarget) (SSHInvocation, error) {
	// The values are written into a config file, so check them again here
	// rather than rely on every caller having used ParseSSHCommand.
	if !sshUserPattern.MatchString(target.User) {
		return SSHInvocation{}, errors.New("invalid user in SSH command")
	}
	if err := validateSSHHost(target.Host); err != nil {
		return SSHInvocation{}, err
	}
	if target.Port != "" {
		if err := validateSSHPort(target.Port); err != nil {
			return SSHInvocation{}, err
		}
	}

	var config strings.Builder
	fmt.Fprintf(&config, "Host *\n\tUser %s\n", target.User)
	if target.Port != "" {
		fmt.Fprintf(&config, "\tPort %s\n", target.Port)
	}

	// Only the current user can open the file (see createPrivateTemp).
	file, err := createPrivateTemp(dir, "daytona-ssh-", ".conf")
	if err != nil {
		return SSHInvocation{}, fmt.Errorf("failed to create SSH config: %w", err)
	}
	if _, err := file.WriteString(config.String()); err != nil {
		_ = file.Close()
		_ = os.Remove(file.Name())
		return SSHInvocation{}, fmt.Errorf("failed to write SSH config: %w", err)
	}
	if err := file.Close(); err != nil {
		_ = os.Remove(file.Name())
		return SSHInvocation{}, fmt.Errorf("failed to write SSH config: %w", err)
	}

	// ssh takes an IPv6 literal without the brackets of "user@[host]".
	host := strings.TrimSuffix(strings.TrimPrefix(target.Host, "["), "]")

	return SSHInvocation{Args: []string{"-F", file.Name(), host}, ConfigPath: file.Name()}, nil
}

// Cleanup removes the temporary ssh_config. It is safe to call more than
// once: a config that is already gone is not an error.
func (i SSHInvocation) Cleanup() error {
	if i.ConfigPath == "" {
		return nil
	}
	if err := os.Remove(i.ConfigPath); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	return nil
}

// cleanupOrWarn removes the config and tells the user when that fails, since
// the file left behind holds the SSH access token.
func cleanupOrWarn(invocation SSHInvocation) {
	if err := invocation.Cleanup(); err != nil {
		fmt.Fprintf(os.Stderr, "Warning: could not remove %s, which holds the SSH access token; delete it manually: %v\n", invocation.ConfigPath, err)
	}
}
