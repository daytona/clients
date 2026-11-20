// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestStartAndControlWhenProcessAcceptsInput(t *testing.T) {
	// Given a live exec process and its control endpoints.
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
		"POST /processes":              jsonHandler(t, processFixture("p/!")),
		"POST /processes/{id}/stdin":   func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) },
		"POST /processes/{id}/signals": func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) },
		"DELETE /processes/{id}":       func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) },
	})
	handle, err := sandbox.Process().Start(t.Context(), "cat", &ProcessStartOptions{Stdin: ProcessStdinOpen, Tag: "task"})
	require.NoError(t, err)
	// When writing exact bytes, closing stdin, signaling with escalation and deleting.
	n, err := handle.Write(t.Context(), []byte{0, 255})
	require.NoError(t, err)
	require.Equal(t, 2, n)
	require.NoError(t, handle.CloseStdin(t.Context()))
	delay := 100*time.Millisecond + time.Nanosecond
	require.NoError(t, handle.Kill(t.Context(), &KillProcessOptions{EscalateAfter: &delay}))
	require.NoError(t, handle.Delete(t.Context()))
	// Then paths are escaped, bytes use base64, and signals have exactly one SIG prefix.
	requests := fake.requests()
	require.JSONEq(t, `{"shellCommand":"cat","tag":"task","stdin":"open","keepLogs":"on_exit_ttl"}`, string(requests[0].Body))
	require.Equal(t, "/processes/p%2F%21/stdin", requests[1].Path)
	require.JSONEq(t, `{"data":"AP8=","encoding":"base64"}`, string(requests[1].Body))
	require.JSONEq(t, `{"data":"","eof":true}`, string(requests[2].Body))
	require.JSONEq(t, `{"signal":"SIGTERM","escalateAfterMs":101,"escalateTo":"SIGKILL"}`, string(requests[3].Body))
	require.Equal(t, "DELETE", requests[4].Method)
}

func TestKillAllRoundsBudgetWhenTagIsValid(t *testing.T) {
	for _, timeout := range []time.Duration{0, time.Nanosecond, 1100 * time.Microsecond, 60 * time.Second} {
		t.Run(timeout.String(), func(t *testing.T) {
			// Given a complete kill reply.
			sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
				"POST /processes:kill": jsonHandler(t, map[string]any{"processIds": []string{"p", "q"}}),
			})
			// When killing a tag with a chosen hold budget.
			ids, err := sandbox.Process().KillAll(t.Context(), "job", &KillAllProcessesOptions{Timeout: timeout})
			// Then the returned ids are preserved and a positive budget is rounded upward.
			require.NoError(t, err)
			require.Equal(t, []string{"p", "q"}, ids)
			var body struct {
				Tag       string
				TimeoutMs int64
			}
			require.NoError(t, json.Unmarshal(fake.requests()[0].Body, &body))
			require.Equal(t, "job", body.Tag)
			require.Equal(t, wireMilliseconds(timeout), body.TimeoutMs)
		})
	}
}

func TestListFiltersLocallyWhenServerIgnoresTag(t *testing.T) {
	// Given tagged and untagged records from an older server.
	a, b, c := processFixture("a"), processFixture("b"), processFixture("c")
	a["tag"], b["tag"] = "wanted", "other"
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
		"GET /processes": jsonHandler(t, map[string]any{"items": []any{a, b, c}}),
	})
	// When listing tagged commands.
	page, err := sandbox.Process().List(t.Context(), &ListProcessesOptions{Tag: "wanted", Kind: ProcessKindExec, State: "running"})
	// Then untagged processes cannot leak into a tag-scoped operation.
	require.NoError(t, err)
	require.Len(t, page.Items, 1)
	require.Equal(t, "a", page.Items[0].ID)
	require.Equal(t, "wanted", fake.requests()[0].Query.Get("tag"))
}

func TestProcessIdentityWhenReconnectedAcrossWorkers(t *testing.T) {
	// Given a JSON identity from either SDK.
	sandbox, fake := processTestSandbox(t, nil)
	var identity ProcessHandleJSON
	require.NoError(t, json.Unmarshal([]byte(`{"sandboxId":"sbx","processId":"p","kind":"code"}`), &identity))
	// When binding locally.
	handle, err := sandbox.Process().ConnectIdentity(identity)
	// Then code replay identity survives without any network request.
	require.NoError(t, err)
	require.Equal(t, ProcessKindCode, handle.Kind())
	require.Equal(t, &identity, handle.ToJSON())
	require.Empty(t, fake.requests())
}

func TestProcessErrorsWhenServerRefusesOperation(t *testing.T) {
	for _, test := range []struct {
		code     string
		status   int
		sentinel error
	}{
		{"PROCESS_NOT_FOUND", 404, ErrProcessNotFound},
		{"PROCESS_TERMINAL", 409, ErrProcessTerminal},
		{"PROCESS_EXECUTION_TIMEOUT", 408, ErrProcessExecutionTimeout},
		{"STDIN_CLOSED", 409, ErrStdinClosed},
		{"SESSION_ENDED", 410, ErrSessionEnded},
		{"KERNEL_UNAVAILABLE", 503, ErrKernelUnavailable},
		{"PROCESS_KILL_INCOMPLETE", 408, ErrProcessKillIncomplete},
	} {
		t.Run(test.code, func(t *testing.T) {
			// Given a daemon error envelope.
			sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{
				"POST /processes": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(test.status)
					writeJSON(t, w, map[string]string{"code": test.code, "message": "refused", "source": "DAYTONA_DAEMON"})
				},
			})
			// When starting work against the refusal.
			_, err := sandbox.Process().Start(t.Context(), "true", nil)
			// Then callers can branch on the exact core code.
			require.ErrorIs(t, err, test.sentinel)
		})
	}
}
