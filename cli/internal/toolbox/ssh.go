// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"fmt"
	"os"
	"strings"
)

// SSHInvocation is what `sandbox ssh` execs: the argv for OpenSSH plus a
// temporary ssh_config that carries the credential-bearing username, so the
// project key never appears in the process list. Call Cleanup when ssh exits.
type SSHInvocation struct {
	Args       []string
	ConfigPath string
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
// `extraArgs` (the user's own ssh flags) come first so they may override.
func BuildSSHInvocation(host string, port int, sandboxID, credential string, extraArgs, remoteCommand []string) (SSHInvocation, error) {
	if strings.ContainsAny(sandboxID+credential+host, "\n\r") {
		return SSHInvocation{}, fmt.Errorf("ssh target contains a line break")
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
	// ssh_config quoting: a value containing spaces or '#' must be double
	// quoted; neither the sandbox id nor the key contain '"'.
	cfg := fmt.Sprintf("Host daytona-sandbox\n\tHostName %s\n\tPort %d\n\tUser \"%s#%s\"\n", host, port, sandboxID, credential)
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
	args = append(args, extraArgs...)
	args = append(args, "daytona-sandbox")
	args = append(args, remoteCommand...)
	return SSHInvocation{Args: args, ConfigPath: f.Name()}, nil
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
