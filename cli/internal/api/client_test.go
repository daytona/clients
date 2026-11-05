// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package api

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
)

func TestDoSendsHeadersAndDecodes(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer tok" || r.Header.Get("X-Daytona-Source") != "cli" ||
			r.Header.Get("X-Daytona-SDK-Version") != "test" || r.Header.Get("User-Agent") != "cli/test" {
			t.Errorf("headers: %v", r.Header)
		}
		if r.URL.RawQuery != "limit=2" {
			t.Errorf("query %q", r.URL.RawQuery)
		}
		_, _ = w.Write([]byte(`{"count":1}`))
	}))
	defer srv.Close()
	c := New(srv.URL+"/", "tok", "test")
	var out map[string]int
	q := map[string][]string{"limit": {"2"}}
	if err := c.Do(context.Background(), "GET", "/x", q, nil, &out); err != nil || out["count"] != 1 {
		t.Fatal(err, out)
	}
}

func TestErrorMapping(t *testing.T) {
	cases := []struct {
		status   int
		body     string
		wantCode int
		hintHas  string
	}{
		{401, `{"code":"UNAUTHORIZED","message":"unauthorized","source":"DAYTONA_API"}`, cerr.ExitAuth, "daytona login"},
		{403, `{"code":"FORBIDDEN","message":"permission denied","source":"DAYTONA_API"}`, cerr.ExitForbidden, "credential is not allowed"},
		{404, `{"code":"NOT_FOUND","message":"not found","source":"DAYTONA_API"}`, cerr.ExitNotFound, "--org/--project"},
		{409, `{"code":"CONFLICT","message":"sandbox status is stopped","source":"DAYTONA_API"}`, cerr.ExitConflict, ""},
		{501, `{"code":"SEARCH_NOT_CONFIGURED","message":"search is not configured","source":"DAYTONA_API"}`, cerr.ExitConflict, "sandbox list"},
		{503, `{"code":"SEARCH_UNAVAILABLE","message":"search is temporarily unavailable","source":"DAYTONA_API"}`, cerr.ExitFailure, "retry"},
		{429, `{"code":"OVERLOADED","message":"platform busy","source":"DAYTONA_API","retry_after_seconds":9}`, cerr.ExitFailure, "retry in 9s"},
		{429, `{"code":"RATE_LIMITED","message":"slow down","source":"DAYTONA_API","retry_after_seconds":7}`, cerr.ExitFailure, "retry in 7s"},
		{429, `{"code":"OVERLOADED","message":"platform busy","source":"DAYTONA_API"}`, cerr.ExitFailure, "retry shortly"},
		{409, `{"code":"NOT_SUPPORTED_FOR_CLASS","message":"this sandbox class does not support the requested operation","source":"DAYTONA_API"}`, cerr.ExitConflict, "class does not support"},
		{402, `{"code":"PROJECT_BUDGET_EXCEEDED","message":"project reached its monthly budget","source":"DAYTONA_API"}`, cerr.ExitFailure, "daytona project budget get"},
		{402, `{"code":"ORG_SUSPENDED","message":"organization suspended","source":"DAYTONA_API"}`, cerr.ExitFailure, ""},
		{500, `{"code":"INTERNAL_SERVER_ERROR","message":"boom","source":"DAYTONA_API","trace_id":"abc"}`, cerr.ExitFailure, "trace id abc"},
		{502, `not json`, cerr.ExitFailure, ""},
	}
	for _, tc := range cases {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(tc.status)
			_, _ = w.Write([]byte(tc.body))
		}))
		c := New(srv.URL, "", "t")
		err := c.Do(context.Background(), "DELETE", "/sandboxes/1", nil, nil, nil)
		srv.Close()
		if cerr.ExitCode(err) != tc.wantCode {
			t.Errorf("%d: exit %d want %d (%v)", tc.status, cerr.ExitCode(err), tc.wantCode, err)
		}
		var ae *APIError
		if !errors.As(err, &ae) || ae.Status != tc.status {
			t.Errorf("%d: APIError not surfaced: %v", tc.status, err)
		}
		var ce *cerr.Error
		errors.As(err, &ce)
		if tc.hintHas != "" && !strings.Contains(ce.Hint, tc.hintHas) {
			t.Errorf("%d: hint %q lacks %q", tc.status, ce.Hint, tc.hintHas)
		}
	}
}

func TestConnectionRefused(t *testing.T) {
	c := New("http://127.0.0.1:1", "", "t")
	err := c.Do(context.Background(), "GET", "/", nil, nil, nil)
	if cerr.ExitCode(err) != cerr.ExitFailure {
		t.Fatal(err)
	}
}

// StreamNoTimeout (follow streams only) outlives the HTTP client's overall
// timeout; plain Stream keeps it, so a stalled non-follow read still fails.
func TestStreamNoTimeout_outlivesHTTPClientTimeoutWhileStreamKeepsIt(t *testing.T) {
	handler := func(w http.ResponseWriter, _ *http.Request) {
		flusher, ok := w.(http.Flusher)
		if !ok {
			w.WriteHeader(http.StatusInternalServerError) // t.Fatal is not allowed on the server goroutine
			return
		}
		_, _ = io.WriteString(w, "first\n")
		flusher.Flush()
		time.Sleep(50 * time.Millisecond)
		_, _ = io.WriteString(w, "second\n")
	}
	srv := httptest.NewServer(http.HandlerFunc(handler))
	defer srv.Close()
	c := New(srv.URL, "tok", "test")
	c.HTTP.Timeout = 10 * time.Millisecond

	resp, err := c.StreamNoTimeout(context.Background(), http.MethodGet, "/logs", nil, nil, "")
	if err != nil {
		t.Fatal(err)
	}
	body, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil || string(body) != "first\nsecond\n" {
		t.Fatalf("no-timeout stream: body=%q err=%v", string(body), err)
	}

	resp, err = c.Stream(context.Background(), http.MethodGet, "/logs", nil, nil, "")
	if err == nil {
		_, err = io.ReadAll(resp.Body)
		resp.Body.Close()
	}
	if err == nil {
		t.Fatal("plain Stream must still be bounded by the client timeout")
	}
}

func TestWithTimeoutAtLeast_onlyLengthensABoundedTimeout(t *testing.T) {
	c := New("http://x", "", "v")
	c.HTTP.Timeout = time.Second
	if got := c.WithTimeoutAtLeast(time.Minute).HTTP.Timeout; got != time.Minute {
		t.Fatalf("lengthened timeout = %v", got)
	}
	if c.HTTP.Timeout != time.Second {
		t.Fatalf("original client changed: %v", c.HTTP.Timeout)
	}
	if got := c.WithTimeoutAtLeast(time.Millisecond).HTTP.Timeout; got != time.Second {
		t.Fatalf("shortened timeout = %v", got)
	}
	c.HTTP.Timeout = 0
	if got := c.WithTimeoutAtLeast(time.Minute).HTTP.Timeout; got != 0 {
		t.Fatalf("unbounded timeout became %v", got)
	}
}
