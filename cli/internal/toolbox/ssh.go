// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"unicode"

	"github.com/daytona/clients/cli/internal/config"
)

// SSHInvocation is what `sandbox ssh` execs: the argv for OpenSSH plus a
// temporary ssh_config that carries the credential-bearing username, so the
// project key never appears in the process list. Call Cleanup when ssh exits.
type SSHInvocation struct {
	Args       []string
	ConfigPath string
}

// SSHTarget is the gateway identity used for a credential-bearing SSH login.
type SSHTarget struct {
	Host, SandboxID, Credential string
	Port                        int
}

// SSHCommand separates local OpenSSH options from the remote command argv.
type SSHCommand struct {
	Options, Args []string
}

// Cleanup removes the temporary config file.
func (i SSHInvocation) Cleanup() {
	if i.ConfigPath != "" {
		_ = os.Remove(i.ConfigPath)
	}
}

// BuildSSHInvocation prepares an OpenSSH call to the sandbox's SSH gateway.
// The gateway protocol carries the credential in the username
// (`<sandbox_id>#<api_key>`); put on the command line that username is
// readable by every local user via `ps`. So the CLI writes it as `User` in a
// 0600 temp ssh_config selected with -F and passes only a host alias in argv.
// Command.Options (the user's own ssh flags) come first so they may override.
func BuildSSHInvocation(target SSHTarget, command SSHCommand) (SSHInvocation, error) {
	dir, err := config.Dir(os.Getenv)
	if err != nil {
		return SSHInvocation{}, err
	}
	return buildSSHInvocation(dir, target, command)
}

// The private directory argument lets parallel tests isolate persistent state
// without changing the process environment.
func buildSSHInvocation(configDir string, target SSHTarget, command SSHCommand) (SSHInvocation, error) {
	host, port := target.Host, target.Port
	sandboxID, credential := target.SandboxID, target.Credential
	if host == "" || strings.ContainsAny(host, "\"'\\%$#") || strings.ContainsFunc(host, func(r rune) bool { return unicode.IsSpace(r) || unicode.IsControl(r) }) {
		return SSHInvocation{}, fmt.Errorf("invalid ssh gateway host")
	}
	if port < 1 || port > 65535 {
		return SSHInvocation{}, fmt.Errorf("invalid ssh gateway port")
	}
	if strings.ContainsAny(sandboxID+credential, "\"\\") || strings.ContainsFunc(sandboxID+credential, unicode.IsControl) {
		return SSHInvocation{}, fmt.Errorf("ssh username contains unsafe config characters")
	}
	knownHosts, err := sshKnownHosts(configDir, host, port)
	if err != nil {
		return SSHInvocation{}, err
	}
	f, err := os.CreateTemp("", "daytona-ssh-*.conf")
	if err != nil {
		return SSHInvocation{}, fmt.Errorf("create ssh config: %w", err)
	}
	if err := f.Chmod(0o600); err != nil {
		_ = f.Close()
		_ = os.Remove(f.Name())
		return SSHInvocation{}, err
	}
	// TOFU: accept-new trusts the initial connection (which can be intercepted),
	// then refuses changed keys. Pin the actual gateway host and port, not the
	// shared daytona-sandbox alias or credential. Neither user nor system host
	// databases participate. Explicit user ssh options may still override this.
	path := strings.NewReplacer("\\", "\\\\", "\"", "\\\"", "%", "%%").Replace(knownHosts)
	cfg := fmt.Sprintf("Host daytona-sandbox\n\tHostName %s\n\tPort %d\n\tUser \"%s#%s\"\n\tStrictHostKeyChecking accept-new\n\tUserKnownHostsFile \"%s\"\n\tGlobalKnownHostsFile none\n", host, port, sandboxID, credential, path)
	if _, err := f.WriteString(cfg); err != nil {
		_ = f.Close()
		_ = os.Remove(f.Name())
		return SSHInvocation{}, fmt.Errorf("write ssh config: %w", err)
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(f.Name())
		return SSHInvocation{}, err
	}
	args := []string{"-F", f.Name()}
	args = append(args, command.Options...)
	args = append(args, "daytona-sandbox")
	args = append(args, command.Args...)
	return SSHInvocation{Args: args, ConfigPath: f.Name()}, nil
}

func sshKnownHosts(configDir, host string, port int) (string, error) {
	dir, err := filepath.Abs(filepath.Join(configDir, "ssh"))
	if err != nil {
		return "", fmt.Errorf("resolve ssh state directory: %w", err)
	}
	// OpenSSH expands ${ENV} even inside quotes; never let a directory setting
	// redirect the trust database through environment-variable expansion.
	if strings.Contains(dir, "${") || strings.ContainsFunc(dir, unicode.IsControl) {
		return "", fmt.Errorf("ssh state directory contains unsafe config characters")
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", fmt.Errorf("create ssh state directory: %w", err)
	}
	st, err := os.Lstat(dir)
	if err != nil {
		return "", fmt.Errorf("stat ssh state directory: %w", err)
	}
	if !st.IsDir() {
		return "", fmt.Errorf("ssh state directory must be a directory, not a symlink")
	}
	if err := os.Chmod(dir, 0o700); err != nil {
		return "", fmt.Errorf("protect ssh state directory: %w", err)
	}
	gateway := net.JoinHostPort(strings.ToLower(host), strconv.Itoa(port))
	path := filepath.Join(dir, fmt.Sprintf("known_hosts-%x", sha256.Sum256([]byte(gateway))))
	if st, err := os.Lstat(path); err == nil {
		if !st.Mode().IsRegular() {
			return "", fmt.Errorf("ssh known-hosts database must be a regular file")
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return "", fmt.Errorf("stat ssh known-hosts database: %w", err)
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return "", fmt.Errorf("open ssh known-hosts database: %w", err)
	}
	if err := f.Chmod(0o600); err != nil {
		_ = f.Close()
		return "", fmt.Errorf("protect ssh known-hosts database: %w", err)
	}
	if err := f.Close(); err != nil {
		return "", fmt.Errorf("close ssh known-hosts database: %w", err)
	}
	return path, nil
}

func ValidateSignalName(value string) (string, error) {
	signal := strings.ToUpper(strings.TrimSpace(value))
	if signal == "" {
		return "", fmt.Errorf("signal is required")
	}
	if strings.HasPrefix(signal, "SIG") {
		return "", fmt.Errorf("use bare signal names like TERM or KILL, not %s", signal)
	}
	switch signal {
	case "TERM", "KILL", "INT", "HUP", "QUIT", "USR1", "USR2":
		return signal, nil
	default:
		return "", fmt.Errorf("unsupported signal %q", value)
	}
}
