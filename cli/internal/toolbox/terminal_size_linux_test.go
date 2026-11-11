// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"os"
	"testing"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/stretchr/testify/require"
	"golang.org/x/sys/unix"
)

func TestTerminalSize_usesDefault_whenEitherDimensionIsZero(t *testing.T) {
	for _, tc := range []struct {
		name string
		size unix.Winsize
		want tbgen.TerminalSize
	}{
		{"zero columns", unix.Winsize{Col: 0, Row: 30}, tbgen.TerminalSize{Cols: 80, Rows: 24}},
		{"zero rows", unix.Winsize{Col: 120, Row: 0}, tbgen.TerminalSize{Cols: 80, Rows: 24}},
		{"valid geometry", unix.Winsize{Col: 120, Row: 30}, tbgen.TerminalSize{Cols: 120, Rows: 30}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			pty, err := os.OpenFile("/dev/ptmx", os.O_RDWR|unix.O_NOCTTY, 0)
			require.NoError(t, err)
			defer pty.Close()
			require.NoError(t, unix.IoctlSetWinsize(int(pty.Fd()), unix.TIOCSWINSZ, &tc.size))
			// When
			size := terminalSize(int(pty.Fd()))
			// Then
			require.Equal(t, tc.want, size)
		})
	}
}
