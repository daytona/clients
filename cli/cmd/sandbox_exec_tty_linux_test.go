// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"golang.org/x/sys/unix"
)

func execTerminal(t *testing.T) (*os.File, *os.File) {
	t.Helper()
	fd, err := unix.Open("/dev/ptmx", unix.O_RDWR|unix.O_NOCTTY|unix.O_CLOEXEC|unix.O_NONBLOCK, 0)
	require.NoError(t, err)
	master := os.NewFile(uintptr(fd), "/dev/ptmx")
	t.Cleanup(func() { require.NoError(t, master.Close()) })
	require.NoError(t, unix.IoctlSetPointerInt(fd, unix.TIOCSPTLCK, 0))
	number, err := unix.IoctlGetInt(fd, unix.TIOCGPTN)
	require.NoError(t, err)
	slave, err := os.OpenFile(fmt.Sprintf("/dev/pts/%d", number), os.O_RDWR, 0)
	require.NoError(t, err)
	oldIn, oldOut := os.Stdin, os.Stdout
	os.Stdin, os.Stdout = slave, slave
	t.Cleanup(func() {
		os.Stdin, os.Stdout = oldIn, oldOut
		require.NoError(t, slave.Close())
	})
	return master, slave
}

func upgradeExecSocket(w http.ResponseWriter, r *http.Request) (net.Conn, *bufio.ReadWriter, error) {
	conn, rw, err := w.(http.Hijacker).Hijack()
	if err != nil {
		return nil, nil, err
	}
	_, err = fmt.Fprintf(rw, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n", sandboxExecWebSocketAccept(r.Header.Get("Sec-WebSocket-Key")))
	if err == nil {
		err = rw.Flush()
	}
	return conn, rw, err
}

func TestExecTTY_replaysEarlyBytesAndResizes_whenTerminalStartsAtZero(t *testing.T) {
	// Given: a real PTY with the zero geometry common in terminal wrappers.
	master, slave := execTerminal(t)
	resized := make(chan struct{})
	terminalChanged := make(chan struct{})
	serverURL := ""
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/sandboxes/sbx":
			_, _ = io.WriteString(w, strings.ReplaceAll(toolboxSandboxDetailFixture("sbx", "linux", "running"), "__SERVER__", serverURL))
		case "/test-tools/sbx/processes":
			var body struct {
				Terminal struct{ Cols, Rows int }
				Timeout  int64 `json:"timeoutMs"`
			}
			assert.NoError(t, json.NewDecoder(r.Body).Decode(&body))
			assert.Equal(t, 80, body.Terminal.Cols)
			assert.Equal(t, 24, body.Terminal.Rows)
			assert.Equal(t, int64(3000), body.Timeout)
			_, _ = io.WriteString(w, `{"id":"proc","state":"running","kind":"pty","stdin":"open","cwd":"/","keepLogs":"until_cleanup","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z"}`)
		case "/test-tools/sbx/processes/proc/logs":
			assert.Equal(t, "base64", r.URL.Query().Get("encoding"))
			_, _ = io.WriteString(w, `{"frames":[{"data":"ZWFybHkK","channel":"stdout","cursor":"c1","timestamp":1}],"nextCursor":"c1","truncatedHead":false,"eof":false}`)
		case "/test-tools/sbx/processes/proc/attach":
			assert.Equal(t, "c1", r.URL.Query().Get("cursor"))
			conn, _, err := upgradeExecSocket(w, r)
			if !assert.NoError(t, err) {
				return
			}
			defer conn.Close()
			// Receipt of the early output proves raw mode and the resize watcher
			// are installed before we change the terminal geometry.
			go func() {
				defer close(terminalChanged)
				buf := make([]byte, len("early\n"))
				assert.NoError(t, master.SetReadDeadline(time.Now().Add(2*time.Second)))
				_, err := io.ReadFull(master, buf)
				assert.NoError(t, err)
				assert.Equal(t, "early\n", string(buf))
				assert.NoError(t, unix.IoctlSetWinsize(int(slave.Fd()), unix.TIOCSWINSZ, &unix.Winsize{Col: 90, Row: 25}))
				assert.NoError(t, syscall.Kill(os.Getpid(), syscall.SIGWINCH))
			}()
			select {
			case <-resized:
				_, _ = conn.Write([]byte{0x82, 5, 'l', 'a', 't', 'e', '\n', 0x88, 0})
			case <-r.Context().Done():
			case <-time.After(2 * time.Second):
				t.Error("no terminal resize arrived")
			}
		case "/test-tools/sbx/processes/proc/resize":
			var size struct{ Cols, Rows int }
			assert.NoError(t, json.NewDecoder(r.Body).Decode(&size))
			w.WriteHeader(http.StatusNoContent)
			// The first resize is the initial 80x24 sent at attach; the
			// SIGWINCH one carries the new geometry.
			if size.Cols == 80 && size.Rows == 24 {
				return
			}
			assert.Equal(t, 90, size.Cols)
			assert.Equal(t, 25, size.Rows)
			close(resized)
		case "/test-tools/sbx/processes/proc/wait":
			_, _ = io.WriteString(w, `{"reason":"exited","exitCode":5}`)
		default:
			t.Errorf("unexpected request %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()
	serverURL = srv.URL
	t.Setenv("DAYTONA_CONFIG_DIR", cfgDir(t))
	root := NewRoot(&bytes.Buffer{}, &bytes.Buffer{})
	root.SetArgs([]string{"--api-url", srv.URL, "--api-key", "k", "sandbox", "exec", "sbx", "--tty", "--execution-timeout", "3s", "--", "sh"})
	ctx, cancel := context.WithTimeout(t.Context(), 3*time.Second)
	defer cancel()
	// When
	err := root.ExecuteContext(ctx)
	select {
	case <-terminalChanged:
	case <-ctx.Done():
		t.Fatal("terminal input driver did not finish")
	}
	// Then
	require.Equal(t, 5, cerr.ExitCode(err), "%v", err)
	buf := make([]byte, len("late\n"))
	require.NoError(t, master.SetReadDeadline(time.Now().Add(time.Second)))
	_, err = io.ReadFull(master, buf)
	require.NoError(t, err)
	require.Equal(t, "late\n", string(buf))
}

func TestProcessAttach_keepsExitCode_whenLiveAliasReleased(t *testing.T) {
	// Given
	execTerminal(t)
	serverURL := ""
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/sandboxes/sbx":
			_, _ = io.WriteString(w, strings.ReplaceAll(toolboxSandboxDetailFixture("sbx", "linux", "running"), "__SERVER__", serverURL))
		case "/test-tools/sbx/processes/live-name":
			_, _ = io.WriteString(w, `{"id":"canonical","state":"running","kind":"pty","stdin":"open","cwd":"/","keepLogs":"until_cleanup","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z"}`)
		case "/test-tools/sbx/processes/canonical/attach":
			conn, _, err := upgradeExecSocket(w, r)
			if assert.NoError(t, err) {
				defer conn.Close()
				_, _ = conn.Write([]byte{0x88, 0})
			}
		case "/test-tools/sbx/processes/canonical/resize":
			// The initial terminal size, sent once at attach.
			w.WriteHeader(http.StatusNoContent)
		case "/test-tools/sbx/processes/canonical/wait":
			_, _ = io.WriteString(w, `{"reason":"exited","exitCode":3}`)
		default:
			t.Errorf("unexpected request %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()
	serverURL = srv.URL
	t.Setenv("DAYTONA_CONFIG_DIR", cfgDir(t))
	root := NewRoot(&bytes.Buffer{}, &bytes.Buffer{})
	root.SetArgs([]string{"--api-url", srv.URL, "--api-key", "k", "sandbox", "process", "attach", "sbx", "live-name"})
	// When
	err := root.ExecuteContext(t.Context())
	// Then
	require.Equal(t, 3, cerr.ExitCode(err))
	require.Equal(t, cerr.SourceProcess, cerr.DetailsOf(err).Source)
}

func TestExecTTY_timeoutClosesIdleWebsocketAndReturnsExitTimeout(t *testing.T) {
	// Given: a real PTY on stdin/stdout, so `sandbox exec --tty` runs its PTY
	// path, and a toolbox that attaches and then stays silent.
	execTerminal(t)
	serverURL := ""
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/sandboxes/sbx":
			_, _ = io.WriteString(w, strings.ReplaceAll(toolboxSandboxDetailFixture("sbx", "linux", "running"), "__SERVER__", serverURL))
		case "/test-tools/sbx/processes":
			_, _ = io.WriteString(w, `{"id":"proc","state":"running","kind":"pty","stdin":"open","cwd":"/","keepLogs":"until_cleanup","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z"}`)
		case "/test-tools/sbx/processes/proc/logs":
			_, _ = io.WriteString(w, `{"frames":[],"nextCursor":"c0","truncatedHead":false,"eof":false}`)
		case "/test-tools/sbx/processes/proc/attach":
			conn, rw, err := upgradeExecSocket(w, r)
			if !assert.NoError(t, err) {
				return
			}
			defer conn.Close()
			// Idle until the client gives up and closes the socket.
			_, _, _ = sandboxExecReadServerFrame(rw.Reader)
			_, _ = io.Copy(io.Discard, conn)
		case "/test-tools/sbx/processes/proc/resize":
			w.WriteHeader(http.StatusNoContent)
		case "/test-tools/sbx/processes/proc":
			// Best-effort cleanup of the timed-out process.
			w.WriteHeader(http.StatusNoContent)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()
	serverURL = srv.URL
	// When
	start := time.Now()
	_, stderr, code := run(t, cfgDir(t), nil, "--api-url", srv.URL, "--api-key", "k", "-o", "json", "--timeout", "300ms", "sandbox", "exec", "sbx", "--tty", "--", "bash")
	// Then
	require.Equal(t, cerr.ExitTimeout, code, stderr)
	require.Less(t, time.Since(start), 5*time.Second)
}
