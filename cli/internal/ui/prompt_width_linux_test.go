// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build linux

package ui

import (
	"bytes"
	"os"
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"
	"github.com/stretchr/testify/require"
	"golang.org/x/sys/unix"
)

func TestPromptRender_fitsSingleRow_whenTextExceedsTerminalWidth(t *testing.T) {
	for _, line := range []string{strings.Repeat("x", 120), strings.Repeat("界", 80)} {
		t.Run(line[:3], func(t *testing.T) {
			// Given an actual terminal with only 20 columns.
			pty, err := os.OpenFile("/dev/ptmx", os.O_RDWR|unix.O_NOCTTY, 0)
			require.NoError(t, err)
			defer pty.Close()
			require.NoError(t, unix.IoctlSetWinsize(int(pty.Fd()), unix.TIOCSWINSZ, &unix.Winsize{Col: 20, Row: 24}))
			var out bytes.Buffer
			prompt := terminalPrompt{in: pty, out: &out}
			// When drawing either a confirm or picker line.
			err = prompt.render(line)
			// Then cell width (not byte count) stays below the wrap boundary.
			require.NoError(t, err)
			require.Less(t, ansi.StringWidth(strings.TrimPrefix(out.String(), "\r\x1b[2K")), 20)
		})
	}
}
