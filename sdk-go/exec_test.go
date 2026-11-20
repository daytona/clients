// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestExecCollectsChannelsWhenExitIsNonzero(t *testing.T) {
	// Given retained output includes provisioning and rich frames.
	fixture := processFixture("p")
	fixture["state"], fixture["reason"], fixture["exitCode"] = "terminal", "exited", 7
	fixture["logs"] = processLogsFixture(
		processFrameFixture("setup", "setup"), processFrameFixture("stdout", "hello"),
		processFrameFixture("stderr", "warning"), processFrameFixture("stdout", "\n"),
		processFrameFixture("display", `{"text/plain":"rich"}`),
	)
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{"POST /processes": jsonHandler(t, fixture)})
	// When executing the obvious shell path.
	result, err := sandbox.Exec(t.Context(), "printf hello; exit 7", nil)
	// Then a failed command is a successful observation with channel-separated output.
	require.NoError(t, err)
	require.Equal(t, 7, *result.ExitCode)
	require.Equal(t, "hello\n", result.Stdout)
	require.Equal(t, "warning", result.Stderr)
	require.Equal(t, "hellowarning\n", result.Output)
	require.Len(t, result.Logs, 5)
	require.Equal(t, "60000", fake.requests()[0].Query.Get("waitMs"))
	require.Equal(t, "text", fake.requests()[0].Query.Get("logs"))
	require.JSONEq(t, `{"kind":"exec","shellCommand":"printf hello; exit 7","stdin":"closed","keepLogs":"on_exit_ttl"}`,
		string(fake.requests()[0].Body))
}

func TestExecArgsPreservesArgumentsWhenBypassingShell(t *testing.T) {
	// Given an inline successful response.
	fixture := processFixture("p")
	fixture["state"], fixture["reason"], fixture["exitCode"] = "terminal", "exited", 0
	fixture["logs"] = processLogsFixture()
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{"POST /processes": jsonHandler(t, fixture)})
	login := false
	// When passing shell metacharacters as literal arguments with distinct deadlines.
	_, err := sandbox.ExecArgs(t.Context(), []string{"echo", "$(false)", "two words"}, &ExecOptions{
		Cwd: "/app", Env: map[string]string{"A": "B"}, User: "worker", Shell: "bash", Login: &login,
		Name: "n", Tag: "build-1", Timeout: 1500400 * time.Microsecond, ExecutionTimeout: time.Nanosecond,
		KeepLogs: ProcessKeepLogsUntilCleanup,
	})
	// Then argv is not re-quoted and wait and execution budgets use separate wire fields.
	require.NoError(t, err)
	require.Equal(t, "1501", fake.requests()[0].Query.Get("waitMs"))
	require.JSONEq(t, `{"kind":"exec","argv":["echo","$(false)","two words"],"cwd":"/app","env":{"A":"B"},
		"user":"worker","shell":"bash","login":false,"name":"n","tag":"build-1","timeoutMs":1,
		"stdin":"closed","keepLogs":"until_cleanup"}`, string(fake.requests()[0].Body))
}

func TestExecReplaysPagesWhenInlineLogIsIncomplete(t *testing.T) {
	// Given a running create and two retained pages after wait.
	first := processLogsFixture(processFrameFixture("stdout", "a"))
	first["eof"], first["nextCursor"] = false, "next"
	last := processLogsFixture(processFrameFixture("stderr", "b"))
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
		"POST /processes":       jsonHandler(t, processFixture("p")),
		"GET /processes/p/wait": jsonHandler(t, map[string]any{"reason": "exited", "exitCode": 0}),
		"GET /processes/p/logs": func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Query().Get("cursor") == "" {
				writeJSON(t, w, first)
				return
			}
			writeJSON(t, w, last)
		},
	})
	// When the first hold cannot return the entire result.
	result, err := sandbox.Exec(t.Context(), "command", &ExecOptions{Timeout: WaitForever})
	// Then waiting and replay preserve order without duplicating frames.
	require.NoError(t, err)
	require.Equal(t, "ab", result.Output)
	require.Len(t, fake.requests(), 4)
	require.Equal(t, "0", fake.requests()[0].Query.Get("waitMs"))
	require.Equal(t, "0", fake.requests()[1].Query.Get("timeoutMs"))
	require.Equal(t, "next", fake.requests()[3].Query.Get("cursor"))
}

func TestExecBoundsSnapshotWhenCallerBudgetExpires(t *testing.T) {
	// Given a still-running writer whose pages always continue.
	pages := 0
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
		"POST /processes": jsonHandler(t, processFixture("p")),
		"GET /processes/p/logs": func(w http.ResponseWriter, _ *http.Request) {
			pages++
			page := processLogsFixture(processFrameFixture("stdout", "x"))
			page["nextCursor"], page["eof"] = string(rune('a'+pages)), false
			writeJSON(t, w, page)
		},
	})
	// When the caller budget expires during creation, but execution has no deadline.
	result, err := sandbox.Exec(t.Context(), "forever", &ExecOptions{Timeout: time.Nanosecond})
	// Then at most eight pages are read, no zero-budget infinite wait or kill is sent.
	require.NoError(t, err)
	require.Equal(t, ProcessReasonTimedOut, result.Reason)
	require.Nil(t, result.ExitCode)
	require.Len(t, result.Logs, 8)
	require.Len(t, fake.requests(), 9)
}

func TestExecStreamsBeforeCompletionWhenWritersAreSet(t *testing.T) {
	// Given an SSE endpoint whose first write is observed by a live writer.
	var output bytes.Buffer
	sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{
		"POST /processes":       jsonHandler(t, processFixture("p")),
		"GET /processes/p/wait": jsonHandler(t, map[string]any{"reason": "exited", "exitCode": 2}),
		"GET /processes/p/logs": func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "text/event-stream")
			frames := []map[string]any{processFrameFixture("stdout", "out"), processFrameFixture("stderr", "err")}
			for _, frame := range frames {
				raw, err := json.Marshal(frame)
				if err != nil {
					t.Error(err)
					return
				}
				if _, err = w.Write(append(append([]byte("event: log\ndata: "), raw...), []byte("\n\n")...)); err != nil {
					t.Error(err)
					return
				}
				if err = http.NewResponseController(w).Flush(); err != nil {
					t.Error(err)
					return
				}
			}
			if _, err := w.Write([]byte("event: eof\ndata: {\"cursor\":\"end\"}\n\n")); err != nil {
				t.Error(err)
			}
		},
	})
	// When both output channels use the same ordinary, non-concurrent writer.
	result, err := sandbox.Exec(t.Context(), "command", &ExecOptions{Stdout: &output, Stderr: &output})
	// Then writers and the collected result see the same channel order.
	require.NoError(t, err)
	require.Equal(t, "outerr", output.String())
	require.Equal(t, "outerr", result.Output)
	require.Equal(t, 2, *result.ExitCode)
}
