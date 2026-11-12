// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"bytes"
	"context"
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
)

func TestMapError_daemonEnvelope(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name       string
		status     int
		body       string
		wantCode   int
		wantSubstr string
	}{
		{
			name:       "401 → ExitAuth",
			status:     http.StatusUnauthorized,
			body:       `{"code":"UNAUTHORIZED","message":"bad token","source":"DAYTONA_PROXY"}`,
			wantCode:   cerr.ExitAuth,
			wantSubstr: "bad token",
		},
		{
			name:       "403 → ExitForbidden",
			status:     http.StatusForbidden,
			body:       `{"code":"FORBIDDEN","message":"denied","source":"DAYTONA_PROXY"}`,
			wantCode:   cerr.ExitForbidden,
			wantSubstr: "denied",
		},
		{
			name:       "404 → ExitNotFound",
			status:     http.StatusNotFound,
			body:       `{"code":"PROCESS_NOT_FOUND","message":"no such process","source":"DAYTONA_DAEMON"}`,
			wantCode:   cerr.ExitNotFound,
			wantSubstr: "PROCESS_NOT_FOUND",
		},
		{
			name:       "409 → ExitConflict",
			status:     http.StatusConflict,
			body:       `{"code":"PROCESS_TERMINAL","message":"already done","source":"DAYTONA_DAEMON"}`,
			wantCode:   cerr.ExitConflict,
			wantSubstr: "PROCESS_TERMINAL",
		},
		{
			name:       "service unavailable stays a failure",
			status:     http.StatusServiceUnavailable,
			body:       `{"code":"SERVICE_UNAVAILABLE","message":"service unavailable","source":"DAYTONA_PROXY"}`,
			wantCode:   cerr.ExitFailure,
			wantSubstr: "SERVICE_UNAVAILABLE",
		},
		{
			name:       "400 → ExitUsage",
			status:     http.StatusBadRequest,
			body:       `{"code":"BAD_REQUEST","message":"invalid field","source":"DAYTONA_PROXY"}`,
			wantCode:   cerr.ExitUsage,
			wantSubstr: "invalid field",
		},
		{
			name:       "500 → ExitFailure",
			status:     http.StatusInternalServerError,
			body:       `{"code":"INTERNAL_SERVER_ERROR","message":"boom","source":"DAYTONA_PROXY"}`,
			wantCode:   cerr.ExitFailure,
			wantSubstr: "boom",
		},
		{
			name:       "empty body uses status text",
			status:     http.StatusServiceUnavailable,
			body:       ``,
			wantCode:   cerr.ExitFailure,
			wantSubstr: "Service Unavailable",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			resp := &http.Response{
				StatusCode: tc.status,
				Body:       io.NopCloser(strings.NewReader(tc.body)),
				Header:     http.Header{},
			}
			err := mapError("GET", "/test", resp)
			var ce *cerr.Error
			if !errors.As(err, &ce) {
				t.Fatalf("expected *cerr.Error, got %T: %v", err, err)
			}
			if ce.Code != tc.wantCode {
				t.Fatalf("code=%d want %d", ce.Code, tc.wantCode)
			}
			if !strings.Contains(err.Error(), tc.wantSubstr) {
				t.Fatalf("error=%q missing %q", err.Error(), tc.wantSubstr)
			}
		})
	}
}

func TestMapError_stoppedSandbox(t *testing.T) {
	t.Parallel()
	resp := &http.Response{
		StatusCode: http.StatusConflict,
		Body:       io.NopCloser(strings.NewReader(`{"code":"SANDBOX_NOT_RUNNING","message":"sandbox is not running","source":"DAYTONA_PROXY"}`)),
		Header:     http.Header{},
	}
	err := mapError("GET", "/files", resp)
	var ce *cerr.Error
	if !errors.As(err, &ce) {
		t.Fatalf("expected *cerr.Error, got %T: %v", err, err)
	}
	if ce.Code != cerr.ExitConflict {
		t.Fatalf("code=%d want %d (conflict, not not-found)", ce.Code, cerr.ExitConflict)
	}
	if !strings.Contains(ce.Hint, "start the sandbox") {
		t.Fatalf("hint=%q missing the start advice", ce.Hint)
	}
	var de *DaemonError
	if !errors.As(err, &de) || de.Code != api.CodeSandboxNotRunning || de.Source != "DAYTONA_PROXY" {
		t.Fatalf("daemon error=%+v", de)
	}
}

func TestMapError_codeInMessage(t *testing.T) {
	t.Parallel()
	// When the message already contains the code, don't duplicate it.
	resp := &http.Response{
		StatusCode: http.StatusNotFound,
		Body:       io.NopCloser(strings.NewReader(`{"code":"PROCESS_NOT_FOUND","message":"PROCESS_NOT_FOUND: no such id","source":"DAYTONA_DAEMON"}`)),
		Header:     http.Header{},
	}
	err := mapError("GET", "/proc", resp)
	msg := err.Error()
	// Should NOT have "PROCESS_NOT_FOUND: PROCESS_NOT_FOUND: no such id"
	if strings.Count(msg, "PROCESS_NOT_FOUND") > 1 {
		t.Fatalf("duplicated code in message: %q", msg)
	}
}

func TestHintFor_statusHints(t *testing.T) {
	t.Parallel()
	cases := []struct {
		status int
		env    daemonErrorEnvelope
		want   string
	}{
		{http.StatusUnauthorized, daemonErrorEnvelope{}, "credential rejected"},
		{http.StatusForbidden, daemonErrorEnvelope{}, "not allowed"},
		{http.StatusNotFound, daemonErrorEnvelope{}, "check the sandbox"},
		{http.StatusConflict, daemonErrorEnvelope{}, "not in a state"},
		{http.StatusConflict, daemonErrorEnvelope{ErrorEnvelope: tbgen.ErrorEnvelope{Code: api.CodeSandboxNotRunning}}, "start the sandbox"},
		{http.StatusConflict, daemonErrorEnvelope{ErrorEnvelope: tbgen.ErrorEnvelope{Code: api.CodeCursorExpired}, FirstAvailableCursor: stringPtr("cur-2")}, "cur-2"},
		{http.StatusServiceUnavailable, daemonErrorEnvelope{ErrorEnvelope: tbgen.ErrorEnvelope{Code: api.CodeOverloaded}}, ""},
		{http.StatusOK, daemonErrorEnvelope{}, ""},
	}
	for _, tc := range cases {
		got := hintFor(tc.status, tc.env)
		if tc.want == "" && got != "" {
			t.Errorf("status=%d: got hint %q, want empty", tc.status, got)
		}
		if tc.want != "" && !strings.Contains(got, tc.want) {
			t.Errorf("status=%d: hint=%q missing %q", tc.status, got, tc.want)
		}
	}
}

func TestDecodeJSON_nilOut(t *testing.T) {
	t.Parallel()
	// nil output drains the reader without error.
	err := decodeJSON(strings.NewReader(`{"name":"test"}`), nil)
	if err != nil {
		t.Fatal(err)
	}
}

func TestDecodeJSON_validPayload(t *testing.T) {
	t.Parallel()
	var out struct {
		Name string `json:"name"`
	}
	err := decodeJSON(strings.NewReader(`{"name":"test"}`), &out)
	if err != nil {
		t.Fatal(err)
	}
	if out.Name != "test" {
		t.Fatalf("name=%q", out.Name)
	}
}

func TestDecodeJSON_invalidPayload(t *testing.T) {
	t.Parallel()
	var out struct{ X int }
	err := decodeJSON(strings.NewReader(`not json`), &out)
	if err == nil {
		t.Fatal("expected error")
	}
}

func TestDurationToMilliseconds(t *testing.T) {
	t.Parallel()
	if got := durationToMilliseconds(5 * time.Second); got != 5000 {
		t.Fatalf("got %d", got)
	}
	if got := durationToMilliseconds(100 * time.Millisecond); got != 100 {
		t.Fatalf("got %d", got)
	}
}

func TestIsTimeoutError(t *testing.T) {
	t.Parallel()
	if isTimeoutError(errors.New("plain")) {
		t.Fatal("plain error should not be timeout")
	}
	if !isTimeoutError(&fakeTimeout{}) {
		t.Fatal("fakeTimeout should be timeout")
	}
}

type fakeTimeout struct{}

func (e *fakeTimeout) Error() string   { return "timeout" }
func (e *fakeTimeout) Timeout() bool   { return true }
func (e *fakeTimeout) Temporary() bool { return false }

func TestClient_GetJSON_success(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Fatalf("method=%s", r.Method)
		}
		if r.Header.Get("Authorization") != "Bearer tok" {
			t.Fatalf("auth=%q", r.Header.Get("Authorization"))
		}
		if r.Header.Get("X-Daytona-Source") != "cli" || r.Header.Get("X-Daytona-SDK-Version") != "test" || r.Header.Get("User-Agent") != "cli/test" {
			t.Errorf("identity headers: %v", r.Header)
		}
		_ = json.NewEncoder(w).Encode(map[string]string{"name": "test"})
	}))
	defer srv.Close()

	c := New(api.New(srv.URL, "tok", "test"))
	var out map[string]string
	if err := c.GetJSON(context.Background(), "/test", nil, &out); err != nil {
		t.Fatal(err)
	}
	if out["name"] != "test" {
		t.Fatalf("out=%v", out)
	}
}

func TestClient_PostJSON_noContent(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	c := New(api.New(srv.URL, "tok", "test"))
	// out is non-nil but should be skipped for 204.
	var out map[string]string
	if err := c.PostJSON(context.Background(), "/test", nil, map[string]string{"name": "test"}, &out); err != nil {
		t.Fatal(err)
	}
}

func TestClient_PostJSON_nilBody(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if len(body) != 0 {
			t.Fatalf("expected empty body, got %q", body)
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	c := New(api.New(srv.URL, "tok", "test"))
	if err := c.PostJSON(context.Background(), "/test", nil, nil, nil); err != nil {
		t.Fatal(err)
	}
}

func TestClient_Delete(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodDelete {
			t.Fatalf("method=%s", r.Method)
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	c := New(api.New(srv.URL, "tok", "test"))
	if err := c.Delete(context.Background(), "/thing/1"); err != nil {
		t.Fatal(err)
	}
}

func TestClient_Stream(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte("data: hello\n\n"))
	}))
	defer srv.Close()

	c := New(api.New(srv.URL, "tok", "test"))
	resp, err := c.Stream(context.Background(), http.MethodGet, "/events", nil, nil, "")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if !strings.Contains(string(body), "hello") {
		t.Fatalf("body=%q", body)
	}
}

func TestClient_do_nonSuccessStatus(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"code":"PROCESS_NOT_FOUND","message":"gone"}`))
	}))
	defer srv.Close()

	c := New(api.New(srv.URL, "tok", "test"))
	var out map[string]string
	err := c.GetJSON(context.Background(), "/missing", nil, &out)
	if err == nil {
		t.Fatal("expected error")
	}
	var ce *cerr.Error
	if !errors.As(err, &ce) {
		t.Fatalf("expected *cerr.Error, got %T", err)
	}
	if ce.Code != cerr.ExitNotFound {
		t.Fatalf("code=%d", ce.Code)
	}
}

func TestClient_do_ctxCancelled(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(5 * time.Second)
	}))
	defer srv.Close()

	c := New(api.New(srv.URL, "tok", "test"))
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err := c.GetJSON(ctx, "/slow", nil, nil)
	if err == nil {
		t.Fatal("expected error")
	}
	var ce *cerr.Error
	if !errors.As(err, &ce) {
		t.Fatalf("expected *cerr.Error, got %T: %v", err, err)
	}
	if ce.Code != cerr.ExitInterrupted {
		t.Fatalf("code=%d want %d", ce.Code, cerr.ExitInterrupted)
	}
}

func TestClient_do_debugCallback(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("{}"))
	}))
	defer srv.Close()

	var debugLines []string
	inner := api.New(srv.URL, "tok", "test")
	inner.Debug = func(format string, a ...any) {
		debugLines = append(debugLines, format)
	}
	c := New(inner)
	_ = c.GetJSON(context.Background(), "/test", nil, &json.RawMessage{})
	if len(debugLines) < 2 {
		t.Fatalf("expected at least 2 debug lines, got %d", len(debugLines))
	}
}

func TestClient_do_queryParams(t *testing.T) {
	t.Parallel()
	var gotQuery string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("{}"))
	}))
	defer srv.Close()

	c := New(api.New(srv.URL, "tok", "test"))
	_ = c.GetJSON(context.Background(), "/test", map[string][]string{"a": {"1"}, "b": {"2"}}, &json.RawMessage{})
	if !strings.Contains(gotQuery, "a=1") || !strings.Contains(gotQuery, "b=2") {
		t.Fatalf("query=%q", gotQuery)
	}
}

func TestClient_BaseURL(t *testing.T) {
	t.Parallel()
	c := New(api.New("http://example.com", "", "v"))
	if c.BaseURL() != "http://example.com" {
		t.Fatalf("baseURL=%q", c.BaseURL())
	}
}

func TestClient_Credential(t *testing.T) {
	t.Parallel()
	c := New(api.New("http://example.com", "secret", "v"))
	if c.Credential() != "secret" {
		t.Fatalf("credential=%q", c.Credential())
	}
}

func TestClient_do_noCredentialHeader(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "" {
			t.Fatalf("unexpected auth header: %q", r.Header.Get("Authorization"))
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("{}"))
	}))
	defer srv.Close()

	// Empty credential → no Authorization header.
	c := New(api.New(srv.URL, "", "test"))
	_ = c.GetJSON(context.Background(), "/test", nil, &bytes.Buffer{})
}
