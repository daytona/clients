// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha1"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	apiinternal "github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	toolboxclient "github.com/daytona/clients/cli/internal/toolbox"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

type toolboxExpectedRequest struct {
	method              string
	path                string
	query               url.Values
	status              int
	response            string
	responseContentType string
	auth                bool
	check               func(*testing.T, *http.Request, []byte)
}

type runToolboxCLIOptions struct {
	output string
}

var toolboxEnvMu sync.Mutex

func runToolboxCLI(t *testing.T, args []string, expected []toolboxExpectedRequest) (string, string, error) {
	t.Helper()
	return runToolboxCLIWithOptions(t, runToolboxCLIOptions{output: "json"}, args, expected)
}

func runToolboxCLIWithOutput(t *testing.T, output string, args []string, expected []toolboxExpectedRequest) (string, string, error) {
	t.Helper()
	return runToolboxCLIWithOptions(t, runToolboxCLIOptions{output: output}, args, expected)
}

func runToolboxCLIWithOptions(t *testing.T, opts runToolboxCLIOptions, args []string, expected []toolboxExpectedRequest) (string, string, error) {
	t.Helper()
	var index int
	serverURL := ""
	// The handler runs on the server's goroutine: use assert (t.Errorf), never
	// require (FailNow is only valid on the test goroutine). An unexpected
	// request answers 500 so the CLI side fails too and the test goroutine's
	// own assertions report the mismatch.
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if index >= len(expected) {
			t.Errorf("unexpected request %s %s", r.Method, r.URL.String())
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		exp := expected[index]
		index++
		assert.Equal(t, exp.method, r.Method)
		assert.Equal(t, exp.path, r.URL.Path)
		if exp.query != nil {
			assert.Equal(t, exp.query, r.URL.Query())
		}
		if exp.auth {
			assert.Equal(t, "Bearer k", r.Header.Get("Authorization"))
		} else {
			assert.Empty(t, r.Header.Get("Authorization"))
		}
		body, err := io.ReadAll(r.Body)
		if !assert.NoError(t, err) {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		if exp.check != nil {
			exp.check(t, r, body)
		}
		// Same contract harness as the fakeAPI, for both API and toolbox requests.
		validateRequestAgainstContract(t, r, body)
		rendered := strings.ReplaceAll(exp.response, "__SERVER__", serverURL)
		if exp.response != "" {
			contentType := exp.responseContentType
			if contentType == "" {
				contentType = "application/json"
			}
			w.Header().Set("Content-Type", contentType)
			validateAgainstContractRequest(t, r, exp.status, []byte(rendered), contentType)
		}
		w.WriteHeader(exp.status)
		_, _ = io.WriteString(w, rendered)
	}))
	defer server.Close()
	serverURL = server.URL

	toolboxEnvMu.Lock()
	defer toolboxEnvMu.Unlock()
	previous := os.Getenv("DAYTONA_CONFIG_DIR")
	t.Cleanup(func() {
		_ = os.Setenv("DAYTONA_CONFIG_DIR", previous)
	})
	require.NoError(t, os.Setenv("DAYTONA_CONFIG_DIR", t.TempDir()))

	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	root := NewRoot(stdout, stderr)
	output := opts.output
	if output == "" {
		output = "json"
	}
	root.SetArgs(append([]string{"--api-url", server.URL, "--api-key", "k", "-o", output}, args...))
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	err := root.ExecuteContext(ctx)
	if err == nil {
		require.Equal(t, len(expected), index)
	}
	return stdout.String(), stderr.String(), err
}

func TestToolboxExec_propagatesExitCode(t *testing.T) {
	stdinFile, openErr := os.CreateTemp(t.TempDir(), "stdin-*.txt")
	require.NoError(t, openErr)
	require.NoError(t, stdinFile.Close())
	stdinFile, openErr = os.Open(stdinFile.Name())
	require.NoError(t, openErr)
	defer stdinFile.Close()
	oldStdin := os.Stdin
	os.Stdin = stdinFile
	defer func() { os.Stdin = oldStdin }()

	stdout, _, err := runToolboxCLI(t, []string{"sandbox", "exec", "sbx", "--", "sh", "-c", "exit 7"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodPost, path: "/test-tools/sbx/processes", status: 201, auth: true, query: url.Values{"logs": {"text"}, "waitMs": {"1000"}}, response: `{"id":"proc","state":"terminal","kind":"exec","stdin":"closed","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","reason":"exited","exitCode":7,"logs":{"frames":[{"channel":"stdout","cursor":"c1","data":"hello\n","timestamp":1}],"nextCursor":"c1","truncatedHead":false,"eof":true}}`, check: func(t *testing.T, _ *http.Request, body []byte) {
			var payload map[string]any
			assert.NoError(t, json.Unmarshal(body, &payload))
			assert.Equal(t, []any{"sh", "-c", "exit 7"}, payload["argv"])
			assert.Equal(t, "open", payload["stdin"])
		}},
	})
	require.Equal(t, 7, cerr.ExitCode(err))
	require.Equal(t, "hello\n", stdout)
}

func TestToolboxExec_forwardsRegularFileStdin(t *testing.T) {
	file, err := os.CreateTemp(t.TempDir(), "stdin-*.txt")
	require.NoError(t, err)
	_, err = file.WriteString("hello")
	require.NoError(t, err)
	require.NoError(t, file.Close())
	file, err = os.Open(file.Name())
	require.NoError(t, err)
	defer file.Close()
	oldStdin := os.Stdin
	os.Stdin = file
	defer func() { os.Stdin = oldStdin }()

	// The process response reports `cat` as RUNNING after the inline wait
	// (it is waiting for stdin), the CLI pumps data + EOF, then follows the
	// log stream to the exit.
	var stdinPosts []map[string]any
	var mu sync.Mutex
	serverURL := ""
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/sbx":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, strings.ReplaceAll(sandboxDetailFixture("sbx", "linux", "running"), "https://toolbox.example", serverURL+"/test-tools"))
		case "/test-tools/sbx/processes":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"id":"proc","state":"running","kind":"exec","stdin":"open","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","logs":{"frames":[],"nextCursor":"c0","truncatedHead":false,"eof":false}}`)
		case "/test-tools/sbx/processes/proc/stdin":
			var payload map[string]any
			body, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(body, &payload)
			mu.Lock()
			stdinPosts = append(stdinPosts, payload)
			mu.Unlock()
			w.WriteHeader(http.StatusNoContent)
		case "/test-tools/sbx/processes/proc/logs":
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = io.WriteString(w, "event: log\nid: c1\ndata: {\"channel\":\"stdout\",\"data\":\"hello\",\"cursor\":\"c1\"}\n\n")
			_, _ = io.WriteString(w, "event: state\ndata: {\"reason\":\"exited\",\"exitCode\":0}\n\n")
			_, _ = io.WriteString(w, "event: eof\ndata: {}\n\n")
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.String())
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	serverURL = server.URL
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", server.URL, "--api-key", "k", "sandbox", "exec", "sbx", "--", "cat")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, "hello", stdout)
	mu.Lock()
	defer mu.Unlock()
	require.Len(t, stdinPosts, 2)
	require.Equal(t, "aGVsbG8=", stdinPosts[0]["data"])
	require.Equal(t, false, stdinPosts[0]["eof"])
	require.Equal(t, true, stdinPosts[1]["eof"])
}

// A one-shot command exits within the inline wait,
// so the create response is already TERMINAL. With stdin redirected (the
// agent/CI case) the CLI must not pump stdin into a finished process — the
// API would answer 409 PROCESS_TERMINAL and mask the real exit code.
func TestToolboxExec_terminalAtCreateSkipsStdinAndKeepsExitCode(t *testing.T) {
	file, err := os.CreateTemp(t.TempDir(), "stdin-*.txt")
	require.NoError(t, err)
	require.NoError(t, file.Close())
	file, err = os.Open(file.Name())
	require.NoError(t, err)
	defer file.Close()
	oldStdin := os.Stdin
	os.Stdin = file
	defer func() { os.Stdin = oldStdin }()

	serverURL := ""
	var stdinCalls int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/sbx":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, strings.ReplaceAll(sandboxDetailFixture("sbx", "linux", "running"), "https://toolbox.example", serverURL+"/test-tools"))
		case "/test-tools/sbx/processes":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"id":"proc","state":"terminal","kind":"exec","stdin":"open","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","reason":"exited","exitCode":7,"logs":{"frames":[{"channel":"stdout","cursor":"c1","data":"hi\n","timestamp":1}],"nextCursor":"c1","truncatedHead":false,"eof":true}}`)
		case "/test-tools/sbx/processes/proc/stdin":
			atomic.AddInt32(&stdinCalls, 1)
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusConflict)
			_, _ = io.WriteString(w, `{"code":"PROCESS_TERMINAL","message":"process proc already exited","source":"DAYTONA_DAEMON"}`)
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.String())
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	serverURL = server.URL
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", server.URL, "--api-key", "k", "sandbox", "exec", "sbx", "--", "sh", "-c", "echo hi; exit 7")
	require.Equal(t, 7, code, stderr)
	require.Equal(t, "hi\n", stdout)
	require.Equal(t, int32(0), atomic.LoadInt32(&stdinCalls), "no stdin write against a terminal process")
}

// The race the guard above cannot close: the process exits BETWEEN the create
// response (running) and the stdin write. A 409 PROCESS_TERMINAL
// is end-of-input, not a failure — the exit code is the command's.
func TestToolboxExec_lateStdinTerminalIsBenign(t *testing.T) {
	file, err := os.CreateTemp(t.TempDir(), "stdin-*.txt")
	require.NoError(t, err)
	_, _ = file.WriteString("ignored")
	require.NoError(t, file.Close())
	file, err = os.Open(file.Name())
	require.NoError(t, err)
	defer file.Close()
	oldStdin := os.Stdin
	os.Stdin = file
	defer func() { os.Stdin = oldStdin }()

	serverURL := ""
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sandboxes/sbx":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, strings.ReplaceAll(sandboxDetailFixture("sbx", "linux", "running"), "https://toolbox.example", serverURL+"/test-tools"))
		case "/test-tools/sbx/processes":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"id":"proc","state":"running","kind":"exec","stdin":"open","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","logs":{"frames":[],"nextCursor":"c0","truncatedHead":false,"eof":false}}`)
		case "/test-tools/sbx/processes/proc/stdin":
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusConflict)
			_, _ = io.WriteString(w, `{"code":"PROCESS_TERMINAL","message":"process proc already exited","source":"DAYTONA_DAEMON"}`)
		case "/test-tools/sbx/processes/proc/logs":
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = io.WriteString(w, "event: state\ndata: {\"reason\":\"exited\",\"exitCode\":3}\n\n")
			_, _ = io.WriteString(w, "event: eof\ndata: {}\n\n")
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.String())
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	serverURL = server.URL
	_, stderr, code := run(t, cfgDir(t), nil, "--api-url", server.URL, "--api-key", "k", "sandbox", "exec", "sbx", "--", "true")
	require.Equal(t, 3, code, stderr)
}

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

func TestRunTTYExec_timeoutClosesIdleWebsocketAndReturnsExitTimeout(t *testing.T) {
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
		switch r.URL.Path {
		case "/test-tools/sbx/processes":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"id":"proc","state":"running","kind":"pty","stdin":"open","cwd":"/","keepLogs":"until_cleanup","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z"}`)
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
			_ = bufrw.Flush()
			reader := bufio.NewReader(conn)
			_, _, _ = sandboxExecReadServerFrame(reader)
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.String())
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	toolClient := toolboxclient.New(apiinternal.New(server.URL+"/test-tools/sbx", "k", "test"))
	err = runTTYExec(ctx, toolClient, []string{"bash"}, "", nil, "")
	err = timeoutForDeadline(ctx, 100*time.Millisecond, err)
	require.Equal(t, cerr.ExitTimeout, cerr.ExitCode(err))
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

func TestTimeoutForDeadline_preservesInterruptedExitCode(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err := timeoutForDeadline(ctx, time.Second, cerr.New(cerr.ExitInterrupted, "interrupted"))
	require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
}

func TestTimeoutForDeadline_mapsDeadlineExceededToExitTimeout(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Millisecond)
	defer cancel()
	<-ctx.Done()
	err := timeoutForDeadline(ctx, time.Second, context.DeadlineExceeded)
	require.Equal(t, cerr.ExitTimeout, cerr.ExitCode(err))
}
