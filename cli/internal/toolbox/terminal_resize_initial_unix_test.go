// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build linux

package toolbox

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/stretchr/testify/require"
	"golang.org/x/sys/unix"
)

func TestForwardTerminalResizes_sendsInitialGeometry_withoutSignal(t *testing.T) {
	// Given
	sizes := make(chan tbgen.ResizeRequest, 1)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/processes/proc/resize" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		var size tbgen.ResizeRequest
		if err := json.NewDecoder(r.Body).Decode(&size); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		sizes <- size
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()
	client := newToolboxClient(srv)
	// A PTY with a known, non-default geometry stands in for stdout.
	pty, err := os.OpenFile("/dev/ptmx", os.O_RDWR|unix.O_NOCTTY, 0)
	require.NoError(t, err)
	defer pty.Close()
	require.NoError(t, unix.IoctlSetPointerInt(int(pty.Fd()), unix.TIOCSPTLCK, 0))
	number, err := unix.IoctlGetInt(int(pty.Fd()), unix.TIOCGPTN)
	require.NoError(t, err)
	slave, err := os.OpenFile(fmt.Sprintf("/dev/pts/%d", number), os.O_RDWR|unix.O_NOCTTY, 0)
	require.NoError(t, err)
	defer slave.Close()
	require.NoError(t, unix.IoctlSetWinsize(int(slave.Fd()), unix.TIOCSWINSZ, &unix.Winsize{Col: 137, Row: 41}))
	oldStdout := os.Stdout
	os.Stdout = slave
	defer func() { os.Stdout = oldStdout }()
	want := tbgen.ResizeRequest{Cols: 137, Rows: 41}
	// When
	stop := client.forwardTerminalResizes(t.Context(), "proc")
	defer stop()
	// Then
	select {
	case size := <-sizes:
		require.Equal(t, want, size)
	case <-time.After(2 * time.Second):
		t.Fatal("attachment did not send the current geometry before SIGWINCH")
	}
}
