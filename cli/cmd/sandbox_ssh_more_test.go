// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	apiinternal "github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	toolboxclient "github.com/daytona/clients/cli/internal/toolbox"
	"github.com/stretchr/testify/require"
)

func TestSplitSSHArguments_noArgs(t *testing.T) {
	sshArgs, remote, err := splitSSHArguments(nil)
	require.NoError(t, err)
	require.Nil(t, sshArgs)
	require.Nil(t, remote)
}

func TestSplitSSHArguments_onlyRemoteCommand(t *testing.T) {
	sshArgs, remote, err := splitSSHArguments([]string{"ls", "-la"})
	require.NoError(t, err)
	require.Empty(t, sshArgs)
	require.Equal(t, []string{"ls", "-la"}, remote)
}

func TestSplitSSHArguments_sshFlagsAndRemote(t *testing.T) {
	sshArgs, remote, err := splitSSHArguments([]string{"-L", "8080:localhost:80", "-v", "--", "uname", "-a"})
	require.NoError(t, err)
	require.Equal(t, []string{"-L", "8080:localhost:80", "-v"}, sshArgs)
	require.Equal(t, []string{"uname", "-a"}, remote)
}

func TestSplitSSHArguments_longOptionNotConsumed(t *testing.T) {
	sshArgs, remote, err := splitSSHArguments([]string{"--verbose", "ls"})
	require.NoError(t, err)
	require.Equal(t, []string{"--verbose"}, sshArgs)
	require.Equal(t, []string{"ls"}, remote)
}

func TestSplitSSHArguments_valueFlagMissingValue(t *testing.T) {
	_, _, err := splitSSHArguments([]string{"-L"})
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestSplitSSHArguments_compoundFlagNotConsumed(t *testing.T) {
	// -vvv is len > 2 so optionNeedsValue returns false
	sshArgs, remote, err := splitSSHArguments([]string{"-vvv", "ls"})
	require.NoError(t, err)
	require.Equal(t, []string{"-vvv"}, sshArgs)
	require.Equal(t, []string{"ls"}, remote)
}

func TestSplitSSHArguments_dashAloneIsNotFlag(t *testing.T) {
	sshArgs, remote, err := splitSSHArguments([]string{"-", "ls"})
	require.NoError(t, err)
	require.Empty(t, sshArgs)
	require.Equal(t, []string{"-", "ls"}, remote)
}

func TestOptionNeedsValue_knownFlags(t *testing.T) {
	for _, flag := range []string{"-B", "-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J", "-L", "-l", "-m", "-O", "-o", "-P", "-p", "-Q", "-R", "-S", "-W", "-w"} {
		require.True(t, optionNeedsValue(flag), "expected %s to need a value", flag)
	}
}

func TestOptionNeedsValue_unknownFlags(t *testing.T) {
	require.False(t, optionNeedsValue("-v"))
	require.False(t, optionNeedsValue("-N"))
	require.False(t, optionNeedsValue("--port"))
	require.False(t, optionNeedsValue("-vvv"))
}

func TestInteractiveAttachAndWait_waitsWithParentContextAfterWebsocketCloses(t *testing.T) {
	stdinFile, err := os.CreateTemp(t.TempDir(), "stdin-*.txt")
	require.NoError(t, err)
	require.NoError(t, stdinFile.Close())
	stdinFile, err = os.Open(stdinFile.Name())
	require.NoError(t, err)
	defer stdinFile.Close()
	stdoutFile, err := os.CreateTemp(t.TempDir(), "stdout-*.txt")
	require.NoError(t, err)
	defer stdoutFile.Close()
	oldStdin := os.Stdin
	oldStdout := os.Stdout
	os.Stdin = stdinFile
	os.Stdout = stdoutFile
	defer func() {
		os.Stdin = oldStdin
		os.Stdout = oldStdout
	}()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/test-tools/sbx/processes/proc/attach":
			hijacker, ok := w.(http.Hijacker)
			if !ok {
				t.Errorf("http.Hijacker unavailable")
				w.WriteHeader(http.StatusInternalServerError)
				return
			}
			conn, bufrw, hijackErr := hijacker.Hijack()
			if hijackErr != nil {
				t.Errorf("Hijack() error = %v", hijackErr)
				return
			}
			defer conn.Close()
			accept := sandboxExecWebSocketAccept(r.Header.Get("Sec-WebSocket-Key"))
			_, _ = bufrw.WriteString("HTTP/1.1 101 Switching Protocols\r\n")
			_, _ = bufrw.WriteString("Upgrade: websocket\r\n")
			_, _ = bufrw.WriteString("Connection: Upgrade\r\n")
			_, _ = bufrw.WriteString("Sec-WebSocket-Accept: " + accept + "\r\n\r\n")
			// A clean close frame: the stream ended, the process result follows.
			_, _ = bufrw.Write([]byte{0x88, 0})
			_ = bufrw.Flush()
		case "/test-tools/sbx/processes/proc/resize":
			w.WriteHeader(http.StatusNoContent)
		case "/test-tools/sbx/processes/proc/wait":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"reason":"exited","exitCode":3}`)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()
	result, err := toolboxclient.New(apiinternal.New(srv.URL+"/test-tools/sbx", "k", "test")).AttachTerminal(context.Background(), toolboxclient.ProcessAttachment{ID: "proc"})
	require.NoError(t, err)
	require.Equal(t, 3, cerr.ExitCode(exitError(result)))
}
