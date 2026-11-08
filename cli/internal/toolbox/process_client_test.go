// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

// newToolboxClient builds a toolbox.Client backed by the given httptest server.
func newToolboxClient(srv *httptest.Server) *Client {
	return New(api.New(srv.URL, "tok", "test"))
}

func TestCreateProcess_success(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || !strings.HasPrefix(r.URL.Path, "/processes") {
			t.Fatalf("unexpected %s %s", r.Method, r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(tbgen.ProcessDto{
			Id:    "proc-1",
			State: tbgen.StateRunning,
			Kind:  tbgen.Exec,
		})
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	proc, err := c.CreateProcess(context.Background(), tbgen.CreateProcessRequest{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if proc.Id != "proc-1" || proc.State != tbgen.StateRunning {
		t.Fatalf("proc=%+v", proc)
	}
}

func TestCreateProcess_normalizesSignal(t *testing.T) {
	t.Parallel()
	sig := "SIGTERM"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(tbgen.ProcessDto{Id: "p", Signal: &sig, State: tbgen.StateTerminal, Kind: tbgen.Exec})
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	proc, err := c.CreateProcess(context.Background(), tbgen.CreateProcessRequest{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if proc.Signal == nil || *proc.Signal != "TERM" {
		t.Fatalf("signal=%v", proc.Signal)
	}
}

func TestGetProcess_success(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/processes/p1" {
			t.Fatalf("path=%s", r.URL.Path)
		}
		_ = json.NewEncoder(w).Encode(tbgen.ProcessDto{Id: "p1", State: tbgen.StateTerminal, Kind: tbgen.Exec})
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	proc, err := c.GetProcess(context.Background(), "p1")
	if err != nil {
		t.Fatal(err)
	}
	if proc.V.Id != "p1" {
		t.Fatalf("id=%s", proc.V.Id)
	}
}

func TestListProcesses_success(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(tbgen.ProcessListResponse{
			Items: []tbgen.ProcessDto{
				{Id: "a", State: tbgen.StateRunning, Kind: tbgen.Exec},
				{Id: "b", State: tbgen.StateTerminal, Kind: tbgen.Exec},
			},
		})
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	items, err := c.ListProcesses(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 2 || items[0].V.Id != "a" || items[1].V.Id != "b" {
		t.Fatalf("items=%+v", items)
	}
}

func TestWaitProcess_immediateResult(t *testing.T) {
	t.Parallel()
	reason := tbgen.ReasonExited
	code := int32(0)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(tbgen.ExitResult{Reason: &reason, ExitCode: &code})
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	result, err := c.WaitProcess(context.Background(), "p1", 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if result.V.Reason == nil || *result.V.Reason != tbgen.ReasonExited {
		t.Fatalf("reason=%v", result.V.Reason)
	}
	if result.V.ExitCode == nil || *result.V.ExitCode != 0 {
		t.Fatalf("exitCode=%v", result.V.ExitCode)
	}
}

func TestWaitProcess_timeout(t *testing.T) {
	t.Parallel()
	timedOut := tbgen.ReasonTimedOut
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Always return timed_out to force the wait loop to exhaust its deadline.
		_ = json.NewEncoder(w).Encode(tbgen.ExitResult{Reason: &timedOut})
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	_, err := c.WaitProcess(context.Background(), "p1", 50*time.Millisecond)
	if err == nil {
		t.Fatal("expected timeout error")
	}
	var ce *cerr.Error
	if !errors.As(err, &ce) || ce.Code != cerr.ExitTimeout {
		t.Fatalf("err=%v", err)
	}
}

func TestWaitProcess_noTimeoutLoopsUntilDone(t *testing.T) {
	t.Parallel()
	calls := 0
	timedOut := tbgen.ReasonTimedOut
	exited := tbgen.ReasonExited
	code := int32(7)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if calls < 3 {
			_ = json.NewEncoder(w).Encode(tbgen.ExitResult{Reason: &timedOut})
			return
		}
		_ = json.NewEncoder(w).Encode(tbgen.ExitResult{Reason: &exited, ExitCode: &code})
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	result, err := c.WaitProcess(context.Background(), "p1", 0)
	if err != nil {
		t.Fatal(err)
	}
	if result.V.ExitCode == nil || *result.V.ExitCode != 7 {
		t.Fatalf("exitCode=%v", result.V.ExitCode)
	}
	if calls < 3 {
		t.Fatalf("calls=%d want >=3", calls)
	}
}

func TestSignalProcess_wireFormat(t *testing.T) {
	t.Parallel()
	var gotBody tbgen.SignalRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	if err := c.SignalProcess(context.Background(), "p1", "TERM", 5*time.Second, "KILL"); err != nil {
		t.Fatal(err)
	}
	if gotBody.Signal == nil || *gotBody.Signal != "SIGTERM" {
		t.Fatalf("signal=%v", gotBody.Signal)
	}
	if gotBody.EscalateAfterMs == nil || *gotBody.EscalateAfterMs != 5000 {
		t.Fatalf("escalateAfterMs=%v", gotBody.EscalateAfterMs)
	}
	if gotBody.EscalateTo == nil || *gotBody.EscalateTo != "SIGKILL" {
		t.Fatalf("escalateTo=%v", gotBody.EscalateTo)
	}
}

func TestSignalProcess_noEscalation(t *testing.T) {
	t.Parallel()
	var gotBody tbgen.SignalRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	if err := c.SignalProcess(context.Background(), "p1", "INT", 0, ""); err != nil {
		t.Fatal(err)
	}
	if gotBody.EscalateAfterMs != nil {
		t.Fatalf("escalateAfterMs should be nil, got %v", gotBody.EscalateAfterMs)
	}
	if gotBody.EscalateTo != nil {
		t.Fatalf("escalateTo should be nil, got %v", gotBody.EscalateTo)
	}
}

func TestWriteStdin_sendsBase64(t *testing.T) {
	t.Parallel()
	var gotBody tbgen.StdinRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	if err := c.WriteStdin(context.Background(), "p1", []byte("hello"), false); err != nil {
		t.Fatal(err)
	}
	if gotBody.Data == nil {
		t.Fatal("data should not be nil")
	}
	decoded, err := base64.StdEncoding.DecodeString(*gotBody.Data)
	if err != nil {
		t.Fatal(err)
	}
	if string(decoded) != "hello" {
		t.Fatalf("data=%q", decoded)
	}
	if gotBody.Encoding == nil || *gotBody.Encoding != "base64" {
		t.Fatalf("encoding=%v", gotBody.Encoding)
	}
	if gotBody.Eof == nil || *gotBody.Eof != false {
		t.Fatalf("eof=%v", gotBody.Eof)
	}
}

func TestWriteStdin_eofOnly(t *testing.T) {
	t.Parallel()
	var gotBody tbgen.StdinRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	if err := c.WriteStdin(context.Background(), "p1", nil, true); err != nil {
		t.Fatal(err)
	}
	if gotBody.Data != nil {
		t.Fatalf("data should be nil, got %v", gotBody.Data)
	}
	if gotBody.Eof == nil || *gotBody.Eof != true {
		t.Fatalf("eof=%v", gotBody.Eof)
	}
}

func TestPumpStdin(t *testing.T) {
	t.Parallel()
	var chunks []string
	var gotEOF bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body tbgen.StdinRequest
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body.Data != nil {
			decoded, _ := base64.StdEncoding.DecodeString(*body.Data)
			chunks = append(chunks, string(decoded))
		}
		if body.Eof != nil && *body.Eof {
			gotEOF = true
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	input := bytes.NewReader([]byte("stdin-data"))
	if err := c.pumpStdin(context.Background(), "p1", input); err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(chunks, "")
	if joined != "stdin-data" {
		t.Fatalf("chunks=%q", joined)
	}
	if !gotEOF {
		t.Fatal("eof not sent")
	}
}

func TestPumpStdin_ctxCancel(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately
	err := c.pumpStdin(ctx, "p1", &infiniteReader{})
	if err == nil {
		t.Fatal("expected error from cancelled context")
	}
}

// infiniteReader never returns EOF.
type infiniteReader struct{}

func (r *infiniteReader) Read(p []byte) (int, error) {
	for i := range p {
		p[i] = 'x'
	}
	return len(p), nil
}

func TestListPorts(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/port" {
			t.Fatalf("path=%s", r.URL.Path)
		}
		_ = json.NewEncoder(w).Encode(tbgen.PortList{Ports: []int32{8080, 3000}})
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	ports, err := c.ListPorts(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(ports) != 2 || ports[0] != 8080 || ports[1] != 3000 {
		t.Fatalf("ports=%v", ports)
	}
}

func TestReadProcessLogs(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(tbgen.ProcessLogsResponse{
			Frames:     []tbgen.LogFrameDto{{Channel: "stdout", Data: "line1"}},
			NextCursor: "c2",
			Eof:        false,
		})
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	page, err := c.ReadProcessLogs(context.Background(), "p1", nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(page.V.Frames) != 1 || page.V.NextCursor != "c2" || page.V.Eof {
		t.Fatalf("page=%+v", page)
	}
}

func TestToWireSignal(t *testing.T) {
	t.Parallel()
	cases := []struct{ in, want string }{
		{"TERM", "SIGTERM"},
		{"kill", "SIGKILL"},
		{"SIGINT", "SIGINT"},
		{"", ""},
	}
	for _, tc := range cases {
		got := toWireSignal(tc.in)
		if got != tc.want {
			t.Errorf("toWireSignal(%q)=%q want %q", tc.in, got, tc.want)
		}
	}
}

func TestFromWireSignal(t *testing.T) {
	t.Parallel()
	cases := []struct{ in, want string }{
		{"SIGTERM", "TERM"},
		{"SIGKILL", "KILL"},
		{"TERM", "TERM"},
		{"", ""},
	}
	for _, tc := range cases {
		got := fromWireSignal(tc.in)
		if got != tc.want {
			t.Errorf("fromWireSignal(%q)=%q want %q", tc.in, got, tc.want)
		}
	}
}

func TestFollowProcessLogs_sseStream(t *testing.T) {
	t.Parallel()
	reason := tbgen.ReasonExited
	code := int32(0)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.Contains(r.URL.Path, "/logs") {
			t.Fatalf("path=%s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)

		// Send a log frame.
		frame, _ := json.Marshal(tbgen.LogFrameDto{Channel: "stdout", Data: "hello", Cursor: "c1"})
		_, _ = io.WriteString(w, "event: log\nid: c1\ndata: "+string(frame)+"\n\n")

		// Send terminal state.
		statePayload := map[string]any{"cursor": "c2", "state": tbgen.StateTerminal, "reason": reason, "exitCode": code}
		state, _ := json.Marshal(statePayload)
		_, _ = io.WriteString(w, "event: state\ndata: "+string(state)+"\n\n")

		// Send eof.
		_, _ = io.WriteString(w, "event: eof\ndata: {\"cursor\":\"c3\"}\n\n")
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	result, cursor, eof, err := c.FollowProcessLogs(context.Background(), "p1", "", "text", 0, stdout, stderr)
	if err != nil {
		t.Fatal(err)
	}
	if stdout.String() != "hello" {
		t.Fatalf("stdout=%q", stdout.String())
	}
	if cursor != "c3" {
		t.Fatalf("cursor=%q", cursor)
	}
	if !eof {
		t.Fatal("expected eof=true")
	}
	if result.Reason == nil || *result.Reason != reason {
		t.Fatalf("reason=%v", result.Reason)
	}
	if result.ExitCode == nil || *result.ExitCode != code {
		t.Fatalf("exitCode=%v", result.ExitCode)
	}
}

func TestFollowProcessLogs_sseError(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, "event: error\ndata: {\"cursor\":\"c7\",\"message\":\"process gone\"}\n\n")
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	_, cursor, _, err := c.FollowProcessLogs(context.Background(), "p1", "", "text", 0, io.Discard, io.Discard)
	if err == nil || !strings.Contains(err.Error(), "process gone") {
		t.Fatalf("err=%v", err)
	}
	if cursor != "c7" {
		t.Fatalf("cursor=%q", cursor)
	}
}

// A `warning` (expired cursor) is surfaced to the caller with the resume
// position instead of being swallowed, and the follower resumes from
// firstAvailableCursor.
func TestFollowProcessLogs_sseWarningIsSurfaced(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, "event: warning\ndata: {\"cursor\":\"c0\",\"message\":\"heads up\",\"firstAvailableCursor\":\"c1\"}\n\n")
		_, _ = io.WriteString(w, "event: eof\ndata: {\"cursor\":\"c2\"}\n\n")
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	var got []LogWarning
	c.OnLogWarning = func(w LogWarning) { got = append(got, w) }
	_, cursor, eof, err := c.FollowProcessLogs(context.Background(), "p1", "", "text", 0, io.Discard, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].Cursor != "c0" || got[0].FirstAvailableCursor != "c1" || got[0].Message != "heads up" {
		t.Fatalf("warning not surfaced: %+v", got)
	}
	if !eof {
		t.Fatal("expected eof")
	}
	if cursor != "c2" {
		t.Fatalf("cursor=%q", cursor)
	}
}

func TestFollowProcessLogs_threadsFollowLimit(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "9", r.URL.Query().Get("limit"))
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, "event: eof\ndata: {\"cursor\":\"c1\"}\n\n")
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	_, _, eof, err := c.FollowProcessLogs(context.Background(), "p1", "", "text", 9, io.Discard, io.Discard)
	require.NoError(t, err)
	require.True(t, eof)
}

func TestFollowProcessLogs_transportEOFBeforeTerminalFails(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, "event: log\ndata: {\"channel\":\"stdout\",\"cursor\":\"c1\",\"data\":\"hello\",\"timestamp\":1}\n\n")
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	_, cursor, eof, err := c.FollowProcessLogs(context.Background(), "p1", "", "text", 0, io.Discard, io.Discard)
	require.ErrorIs(t, err, ErrProcessLogsStreamEndedEarly)
	require.Equal(t, "c1", cursor)
	require.False(t, eof)
}

func TestFollowProcessLogs_transportEOFAfterTerminalReturnsPartialFollow(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, "event: state\ndata: {\"cursor\":\"c1\",\"state\":\"terminal\",\"reason\":\"exited\",\"exitCode\":0}\n\n")
	}))
	defer srv.Close()

	c := newToolboxClient(srv)
	result, cursor, eof, err := c.FollowProcessLogs(context.Background(), "p1", "", "text", 0, io.Discard, io.Discard)
	require.NoError(t, err)
	require.Equal(t, "c1", cursor)
	require.False(t, eof)
	require.Equal(t, tbgen.ReasonExited, *result.Reason)
}
