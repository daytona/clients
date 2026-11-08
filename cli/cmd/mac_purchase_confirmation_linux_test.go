// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"slices"
	"syscall"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
	"golang.org/x/sys/unix"
)

// This subprocess runs the real command with a controlling terminal. Keeping
// terminal state in the child avoids redirecting the parent test's descriptors.
func TestMacPurchaseTerminalProcess(t *testing.T) {
	if os.Getenv("DAYTONA_TEST_MAC_PURCHASE_TTY") != "1" {
		return
	}
	separator := slices.Index(os.Args, "--")
	if separator < 0 {
		os.Exit(cerr.ExitUsage)
	}
	root := NewRoot(os.Stdout, os.Stderr)
	root.SetArgs(os.Args[separator+1:])
	os.Exit(cerr.ExitCode(root.Execute()))
}

func purchaseTerminal(t *testing.T) (*os.File, *os.File) {
	t.Helper()
	master, err := os.OpenFile("/dev/ptmx", os.O_RDWR|unix.O_NOCTTY, 0)
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, master.Close()) })
	require.NoError(t, unix.IoctlSetPointerInt(int(master.Fd()), unix.TIOCSPTLCK, 0))
	index, err := unix.IoctlGetInt(int(master.Fd()), unix.TIOCGPTN)
	require.NoError(t, err)
	slave, err := os.OpenFile(fmt.Sprintf("/dev/pts/%d", index), os.O_RDWR|unix.O_NOCTTY, 0)
	require.NoError(t, err)
	require.NoError(t, unix.IoctlSetWinsize(int(master.Fd()), unix.TIOCSWINSZ, &unix.Winsize{Row: 40, Col: 120}))
	return master, slave
}

func TestMacPurchase_promptsBeforeHTTP_when_atTerminal(t *testing.T) {
	for _, answer := range []string{"\r", "n\r", "y\r"} {
		for index, args := range macPurchaseCommands() {
			t.Run(fmt.Sprintf("command%d-answer%q", index, answer), func(t *testing.T) {
				t.Parallel()
				// Given
				f := macPurchaseAPI(t)
				master, slave := purchaseTerminal(t)
				ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
				defer cancel()
				command := exec.CommandContext(ctx, os.Args[0], append([]string{"-test.run=^TestMacPurchaseTerminalProcess$", "--"}, args...)...)
				command.Env = append(os.Environ(),
					"DAYTONA_CONFIG_DIR="+cfgDir(t), "DAYTONA_TEST_MAC_PURCHASE_TTY=1",
					"DAYTONA_API_URL=", "DAYTONA_V2_API_URL="+f.srv.URL, "DAYTONA_API_KEY=k",
					"DAYTONA_YES=", "DAYTONA_ORG=", "DAYTONA_PROJECT=", "DAYTONA_REGION=", "DAYTONA_PROFILE=",
					"CI=", "TERM=xterm-256color", "NO_COLOR=1")
				command.Stdin, command.Stdout, command.Stderr = slave, slave, slave
				command.SysProcAttr = &syscall.SysProcAttr{Setsid: true, Setctty: true, Ctty: 0}
				// When
				require.NoError(t, command.Start())
				require.NoError(t, slave.Close())
				var output bytes.Buffer
				prompted := false
				beforeConsent := -1
				buffer := make([]byte, 4096)
				for {
					n, err := master.Read(buffer)
					output.Write(buffer[:n])
					// Bubble Tea's bracketed-paste mode is a terminal protocol
					// signal, not prompt prose. Respond only once the UI starts.
					if !prompted && bytes.Contains(output.Bytes(), []byte("\x1b[?2004h")) {
						prompted = true
						f.mu.Lock()
						beforeConsent = len(f.requests)
						f.mu.Unlock()
						_, writeErr := master.WriteString(answer)
						require.NoError(t, writeErr)
					}
					if err != nil {
						break
					}
				}
				err := command.Wait()
				// Then
				require.NoError(t, ctx.Err(), output.String())
				require.True(t, prompted, output.String())
				require.Zero(t, beforeConsent)
				f.mu.Lock()
				defer f.mu.Unlock()
				if answer == "y\r" {
					require.NoError(t, err, output.String())
					require.Len(t, f.requests, 1)
				} else {
					var exit *exec.ExitError
					require.ErrorAs(t, err, &exit)
					require.Equal(t, cerr.ExitInterrupted, exit.ExitCode())
					require.Empty(t, f.requests)
				}
			})
		}
	}
}
