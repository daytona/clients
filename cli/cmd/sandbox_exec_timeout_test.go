// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bufio"
	"context"
	"crypto/sha1"
	"encoding/base64"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestToolboxExec_timeoutCoversLogFollow(t *testing.T) {
	stdinFile, err := os.CreateTemp(t.TempDir(), "stdin-*.txt")
	require.NoError(t, err)
	require.NoError(t, stdinFile.Close())
	stdinFile, err = os.Open(stdinFile.Name())
	require.NoError(t, err)
	defer stdinFile.Close()
	oldStdin := os.Stdin
	os.Stdin = stdinFile
	defer func() { os.Stdin = oldStdin }()

	serverURL := ""
	// Handler goroutine: report with t.Errorf (never FailNow) — the test
	// goroutine asserts the exit code, which is what a broken request breaks.
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/sbx":
			if got := r.Header.Get("Authorization"); got != "Bearer k" {
				t.Errorf("Authorization = %q, want Bearer k", got)
			}
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, strings.ReplaceAll(sandboxDetailFixture("sbx", "linux", "running"), "https://toolbox.example", serverURL+"/test-tools"))
		case "/test-tools/sbx/processes":
			if r.Method != http.MethodPost {
				t.Errorf("method = %s, want POST", r.Method)
			}
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"id":"proc","state":"running","kind":"exec","stdin":"closed","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","logs":{"frames":[],"nextCursor":"c1","truncatedHead":false,"eof":false}}`)
		case "/test-tools/sbx/processes/proc/stdin":
			w.WriteHeader(http.StatusNoContent)
		case "/test-tools/sbx/processes/proc/logs":
			time.Sleep(time.Second)
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.String())
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	serverURL = server.URL
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", server.URL, "--api-key", "k", "-o", "json", "--timeout", "500ms", "sandbox", "exec", "sbx", "--", "sleep", "1")
	require.Empty(t, stdout)
	require.Equal(t, cerr.ExitTimeout, code, stderr)
}

func TestToolboxExec_timeoutOnInitialSandboxLookupReturnsExitTimeout(t *testing.T) {
	stdinFile, err := os.CreateTemp(t.TempDir(), "stdin-*.txt")
	require.NoError(t, err)
	require.NoError(t, stdinFile.Close())
	stdinFile, err = os.Open(stdinFile.Name())
	require.NoError(t, err)
	defer stdinFile.Close()
	oldStdin := os.Stdin
	os.Stdin = stdinFile
	defer func() { os.Stdin = oldStdin }()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/sandboxes/sbx" {
			t.Errorf("path = %s, want /sandboxes/sbx", r.URL.Path)
		}
		time.Sleep(time.Second)
	}))
	defer server.Close()
	_, stderr, code := run(t, cfgDir(t), nil, "--api-url", server.URL, "--api-key", "k", "-o", "json", "--timeout", "500ms", "sandbox", "exec", "sbx", "--", "sleep", "1")
	require.Equal(t, cerr.ExitTimeout, code, stderr)
}

func sandboxExecWebSocketAccept(key string) string {
	sum := sha1.Sum([]byte(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))
	return base64.StdEncoding.EncodeToString(sum[:])
}

func sandboxExecReadServerFrame(r *bufio.Reader) (byte, []byte, error) {
	header := make([]byte, 2)
	if _, err := io.ReadFull(r, header); err != nil {
		return 0, nil, err
	}
	opcode := header[0] & 0x0F
	masked := header[1]&0x80 != 0
	length := int(header[1] & 0x7F)
	var mask [4]byte
	if masked {
		if _, err := io.ReadFull(r, mask[:]); err != nil {
			return 0, nil, err
		}
	}
	payload := make([]byte, length)
	if _, err := io.ReadFull(r, payload); err != nil {
		return 0, nil, err
	}
	if masked {
		for i := range payload {
			payload[i] ^= mask[i%4]
		}
	}
	return opcode, payload, nil
}

func TestTimeoutForDeadline_mapsCanceledContextToExitInterrupted(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err := timeoutForDeadline(ctx, time.Second, errors.New("unrelated failure"))
	require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
}

func TestTimeoutForDeadline_mapsDeadlineExceededToExitTimeout(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Millisecond)
	defer cancel()
	<-ctx.Done()
	err := timeoutForDeadline(ctx, time.Second, context.DeadlineExceeded)
	require.Equal(t, cerr.ExitTimeout, cerr.ExitCode(err))
}
