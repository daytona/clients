// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/spf13/cobra"
	"github.com/stretchr/testify/require"
)

func TestSandboxLogs_macosLogsRoute(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "logs", "22222222-2222-2222-2222-222222222222"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "macos", "running")},
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222/console-logs", status: 200, response: "text/plain:sandbox log\n"},
	})
	require.NoError(t, err)
	require.Contains(t, stdout, "sandbox log")
}

func TestSandboxLogs_unsupportedClassEntrypointRejected(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "logs", "22222222-2222-2222-2222-222222222222", "--entrypoint"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "unsupported", "running")},
	})
	require.Error(t, err)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
}

func TestSandboxLogs_unsupportedClassLogsRejected(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "logs", "22222222-2222-2222-2222-222222222222"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "unsupported", "running")},
	})
	require.Error(t, err)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
}

func TestSandboxLogs_linuxEntrypointRoute(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "logs", "11111111-1111-1111-1111-111111111111", "--entrypoint"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111/logs", status: 200, response: "text/plain:entrypoint output\n"},
	})
	require.NoError(t, err)
	require.Contains(t, stdout, "entrypoint output")
}

func TestSandboxLogs_linuxNonEntrypointRoute(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "logs", "11111111-1111-1111-1111-111111111111"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111/console-logs", status: 200, response: "text/plain:regular logs\n"},
	})
	require.NoError(t, err)
	require.Contains(t, stdout, "regular logs")
}

func TestSandboxUsage_historyRoute(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "usage", "11111111-1111-1111-1111-111111111111", "--history"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111", status: 200, response: sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running")},
		{method: http.MethodGet, path: "/sandboxes/11111111-1111-1111-1111-111111111111/usage/history", status: 200, response: fixtureJSON(usageHistoryFixture("11111111-1111-1111-1111-111111111111"))},
	})
	require.NoError(t, err)
	out := requireJSONStdout(t, stdout)
	require.NotNil(t, out["periods"])
}

func TestSandboxUsage_historyRejectsNonLinux(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "usage", "22222222-2222-2222-2222-222222222222", "--history"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "macos", "running")},
	})
	require.Error(t, err)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
}

func TestSandboxUsage_macosRoute(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "usage", "22222222-2222-2222-2222-222222222222"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222", status: 200, response: sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "macos", "running")},
		{method: http.MethodGet, path: "/sandboxes/22222222-2222-2222-2222-222222222222/usage", status: 200, response: `{"sandbox_id":"22222222-2222-2222-2222-222222222222","usage":[]}`},
	})
	require.NoError(t, err)
	out := requireJSONStdout(t, stdout)
	require.NotNil(t, out["usage"])
	require.Equal(t, "22222222-2222-2222-2222-222222222222", out["sandbox_id"])
}

func TestSandboxUsage_defaultOutputShowsUsageFields(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/sandboxes/22222222-2222-2222-2222-222222222222", 200, mustJSON(t, sandboxDetailFixture("22222222-2222-2222-2222-222222222222", "macos", "running")))
	f.on("GET", "/sandboxes/22222222-2222-2222-2222-222222222222/usage", 200, map[string]any{"sandbox_id": "22222222-2222-2222-2222-222222222222", "usage": []any{}})
	d := cfgDir(t)
	stdout, _, code := run(t, d, nil, "--api-url", f.srv.URL, "--api-key", "k", "sandbox", "usage", "22222222-2222-2222-2222-222222222222")
	require.Equal(t, 0, code)
	require.Contains(t, stdout, "sandbox_id")
	require.Contains(t, stdout, "22222222-2222-2222-2222-222222222222")
	require.Contains(t, stdout, `[]`)
}

func TestReadSandboxLogs_followStreamsBytesBeforeInterrupt(t *testing.T) {
	oldInterval := sandboxLogsPollInterval
	sandboxLogsPollInterval = 10 * time.Millisecond
	defer func() { sandboxLogsPollInterval = oldInterval }()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var calls int
	seenSecond := make(chan struct{})
	go func() {
		<-seenSecond
		time.Sleep(5 * time.Millisecond)
		cancel()
	}()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls++
		switch calls {
		case 1:
			_, _ = w.Write([]byte("first\n"))
		case 2:
			_, _ = w.Write([]byte("first\nsecond\n"))
			close(seenSecond)
		default:
			t.Fatalf("unexpected poll %d", calls)
		}
	}))
	defer srv.Close()
	cmd := &cobra.Command{}
	cmd.SetContext(ctx)
	stdout := new(strings.Builder)
	stderr := new(strings.Builder)
	_, err := readSandboxLogs(cmd, api.New(srv.URL, "tok", "test"), "/logs", true, output.FormatTable, stdout, stderr)
	require.Error(t, err)
	require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
	require.Equal(t, "first\nsecond\n", stdout.String())
}

func TestReadSandboxLogs_followSanitizesSplitCSISequence(t *testing.T) {
	oldInterval := sandboxLogsPollInterval
	sandboxLogsPollInterval = 10 * time.Millisecond
	defer func() { sandboxLogsPollInterval = oldInterval }()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var calls int
	seenSecond := make(chan struct{})
	go func() {
		<-seenSecond
		time.Sleep(5 * time.Millisecond)
		cancel()
	}()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls++
		switch calls {
		case 1:
			_, _ = w.Write([]byte("first\n\x1b["))
		case 2:
			_, _ = w.Write([]byte("first\n\x1b[31mred\tok\n"))
			close(seenSecond)
		default:
			t.Fatalf("unexpected poll %d", calls)
		}
	}))
	defer srv.Close()
	cmd := &cobra.Command{}
	cmd.SetContext(ctx)
	stdout := new(strings.Builder)
	stderr := new(strings.Builder)
	_, err := readSandboxLogs(cmd, api.New(srv.URL, "tok", "test"), "/logs", true, output.FormatTable, stdout, stderr)
	require.Error(t, err)
	require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
	require.Equal(t, "first\nred\tok\n", stdout.String())
}

func TestReadSandboxLogs_followHandlesTruncatedOverlap(t *testing.T) {
	oldInterval := sandboxLogsPollInterval
	sandboxLogsPollInterval = 10 * time.Millisecond
	defer func() { sandboxLogsPollInterval = oldInterval }()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var calls int
	seenSecond := make(chan struct{})
	go func() {
		<-seenSecond
		time.Sleep(5 * time.Millisecond)
		cancel()
	}()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls++
		switch calls {
		case 1:
			_, _ = w.Write([]byte("alpha\nbeta\n"))
		case 2:
			_, _ = w.Write([]byte("beta\ngamma\n"))
			close(seenSecond)
		default:
			t.Fatalf("unexpected poll %d", calls)
		}
	}))
	defer srv.Close()
	cmd := &cobra.Command{}
	cmd.SetContext(ctx)
	stdout := new(strings.Builder)
	stderr := new(strings.Builder)
	_, err := readSandboxLogs(cmd, api.New(srv.URL, "tok", "test"), "/logs", true, output.FormatTable, stdout, stderr)
	require.Error(t, err)
	require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
	require.Equal(t, "alpha\nbeta\ngamma\n", stdout.String())
	require.Empty(t, stderr.String())
}

func TestReadSandboxLogs_followWarnsWhenSnapshotRollsPastSeenPrefix(t *testing.T) {
	oldInterval := sandboxLogsPollInterval
	sandboxLogsPollInterval = 10 * time.Millisecond
	defer func() { sandboxLogsPollInterval = oldInterval }()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var calls int
	seenSecond := make(chan struct{})
	go func() {
		<-seenSecond
		time.Sleep(5 * time.Millisecond)
		cancel()
	}()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls++
		switch calls {
		case 1:
			_, _ = w.Write([]byte("alpha\n"))
		case 2:
			_, _ = w.Write([]byte("omega\n"))
			close(seenSecond)
		default:
			// A slow machine may poll again before the cancel lands: the
			// same snapshot again adds nothing.
			_, _ = w.Write([]byte("omega\n"))
			cancel()
		}
	}))
	defer srv.Close()
	cmd := &cobra.Command{}
	cmd.SetContext(ctx)
	stdout := new(strings.Builder)
	stderr := new(strings.Builder)
	_, err := readSandboxLogs(cmd, api.New(srv.URL, "tok", "test"), "/logs", true, output.FormatTable, stdout, stderr)
	require.Error(t, err)
	require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
	require.Equal(t, "alpha\nomega\n", stdout.String())
	require.Contains(t, stderr.String(), "warning: log output was truncated between polls")
}

// The overlap scan is linear: a large snapshot with no overlap must not cost
// O(n²) comparisons, which can exhaust client CPU on untrusted responses.
func TestLongestLogOverlap_linearAndCorrect(t *testing.T) {
	cases := []struct {
		seen, current string
		want          int
	}{
		{"", "abc", 0},
		{"abc", "", 0},
		{"hello world", "world peace", 5},
		{"aaaa", "aaaab", 4},
		{"xyz", "abc", 0},
		{"log line 1\nlog line 2\n", "line 2\nlog line 3\n", 7},
		{"abcabc", "abcabcabc", 6},
	}
	for _, c := range cases {
		if got := longestLogOverlap(c.seen, c.current); got != c.want {
			t.Errorf("overlap(%q,%q)=%d want %d", c.seen, c.current, got, c.want)
		}
	}
	seen := strings.Repeat("a", 2<<20)
	current := strings.Repeat("a", 2<<20-1) + "b"
	start := time.Now()
	got := longestLogOverlap(seen, current)
	if el := time.Since(start); el > 2*time.Second {
		t.Fatalf("2 MiB overlap scan took %s (quadratic?)", el)
	}
	if got != 2<<20-1 {
		t.Fatalf("got %d", got)
	}
}
