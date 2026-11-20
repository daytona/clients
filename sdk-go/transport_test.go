// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestTransport_whenSendingJSON(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /echo": jsonHandler(t, map[string]string{"reply": "ok"})})
	client := newTestClient(t, fake.Server)
	var output struct {
		Reply string `json:"reply"`
	}
	// When
	err := client.api().do(t.Context(), apiRequest{
		Method: "POST", Path: "/echo", Body: map[string]string{"message": "hello"},
		Result: &output, Query: url.Values{"q": {"a/b c"}}, IdempotencyKey: "request-one",
		Headers: http.Header{"X-Custom": {"value"}, "Authorization": {"not-the-sdk-token"}},
	})
	// Then
	require.NoError(t, err)
	require.Equal(t, "ok", output.Reply)
	requests := fake.requests()
	require.Len(t, requests, 1)
	request := requests[0]
	require.Equal(t, "a/b c", request.Query.Get("q"))
	require.Equal(t, "sdk-go", request.Header.Get("X-Daytona-Source"))
	require.Equal(t, client.version, request.Header.Get("X-Daytona-SDK-Version"))
	require.Equal(t, "sdk-go/"+client.version, request.Header.Get("User-Agent"))
	require.Equal(t, "request-one", request.Header.Get("Idempotency-Key"))
	require.Equal(t, "value", request.Header.Get("X-Custom"))
	require.Equal(t, "Bearer test-key", request.Header.Get("Authorization"))
	require.JSONEq(t, `{"message":"hello"}`, string(request.Body))
}

func TestTransport_whenRedirectedOrAbsolute(t *testing.T) {
	// Given
	destination := newFakeServer(t, map[string]http.HandlerFunc{"GET /": jsonHandler(t, struct{}{})})
	source := newFakeServer(t, map[string]http.HandlerFunc{"GET /redirect": func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, destination.Server.URL, http.StatusTemporaryRedirect)
	}})
	client := newTestClient(t, source.Server)
	// When
	err := client.api().do(t.Context(), apiRequest{Path: "/redirect"})
	// Then
	var detail *Error
	require.ErrorAs(t, err, &detail)
	require.Equal(t, 307, detail.StatusCode)
	require.Equal(t, SourceSDK, detail.Source)
	require.Empty(t, destination.requests())
	for _, path := range []string{destination.Server.URL, "//evil.invalid/path", `\\evil.invalid\path`, "https:opaque"} {
		require.ErrorIs(t, client.api().do(t.Context(), apiRequest{Path: path}), ErrBadRequest)
	}
	require.Len(t, source.requests(), 1)
}

func TestTransport_whenInjectedClientHasCookies(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /": func(w http.ResponseWriter, _ *http.Request) {
		http.SetCookie(w, &http.Cookie{Name: "session", Value: "server-cookie"})
		writeJSON(t, w, struct{}{})
	}})
	jar, err := cookiejar.New(nil)
	require.NoError(t, err)
	endpoint, err := url.Parse(fake.Server.URL)
	require.NoError(t, err)
	jar.SetCookies(endpoint, []*http.Cookie{{Name: "ambient", Value: "user-cookie"}})
	injected := &http.Client{Jar: jar, Timeout: time.Nanosecond}
	client := newTestClient(t, fake.Server, WithHTTPClient(injected))
	// When
	for range 2 {
		require.NoError(t, client.api().do(t.Context(), apiRequest{Path: "/"}))
	}
	// Then
	for _, request := range fake.requests() {
		require.Empty(t, request.Header.Get("Cookie"))
	}
	require.Len(t, jar.Cookies(endpoint), 1)
	require.Same(t, jar, injected.Jar)
	require.Equal(t, time.Nanosecond, injected.Timeout)
}

func TestTransport_whenJSONResponseVaries(t *testing.T) {
	for _, test := range []struct {
		name, body, contentType string
		want                    error
	}{
		{"valid", `{"ok":true}`, "Application/JSON; charset=utf-8", nil},
		{"empty", "", "application/json", nil},
		{"invalid", "{bad", "application/json", ErrBadRequest},
		{"invalid utf8", "{\"x\":\"\xff\"}", "application/json", ErrBadRequest},
		{"nonstandard", `{"x":NaN}`, "application/json", ErrBadRequest},
		{"text ignored", "{bad", "text/plain", nil},
		{"jsonl ignored", "{bad", "application/jsonl", nil},
		{"600 levels", strings.Repeat("[", 600) + strings.Repeat("]", 600), "application/json", nil},
		{"601 levels", strings.Repeat("[", 601) + strings.Repeat("]", 601), "application/json", ErrBadRequest},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /": func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", test.contentType)
				if _, err := io.WriteString(w, test.body); err != nil {
					t.Error(err)
				}
			}})
			client := newTestClient(t, fake.Server)
			// When
			err := client.api().do(t.Context(), apiRequest{Path: "/"})
			// Then
			if test.want != nil {
				require.ErrorIs(t, err, test.want)
			} else {
				require.NoError(t, err)
			}
		})
	}
}

func TestTransport_whenBufferedBodyExceedsCap(t *testing.T) {
	for _, status := range []int{200, 404} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /": func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(status)
				if _, err := io.WriteString(w, strings.Repeat("x", 100)); err != nil {
					t.Error(err)
				}
			}})
			client := newTestClient(t, fake.Server, WithMaxResponseBytes(20))
			// When
			err := client.api().do(t.Context(), apiRequest{Path: "/"})
			// Then
			if status == 404 {
				require.ErrorIs(t, err, ErrNotFound)
			} else {
				require.ErrorIs(t, err, ErrPayloadTooLarge)
			}
		})
	}
}

func TestTransport_whenCallerCancels(t *testing.T) {
	// Given
	started := make(chan struct{})
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /": func(_ http.ResponseWriter, r *http.Request) {
		close(started)
		<-r.Context().Done()
	}})
	client := newTestClient(t, fake.Server)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- client.api().do(ctx, apiRequest{Path: "/"}) }()
	<-started
	// When
	cancel()
	// Then
	err := <-done
	require.ErrorIs(t, err, context.Canceled)
	require.NotErrorIs(t, err, ErrClientTimeout)
}

func TestTransport_whenRequestDeadlineExpires(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /": func(_ http.ResponseWriter, r *http.Request) {
		<-r.Context().Done()
	}})
	client := newTestClient(t, fake.Server, WithRequestTimeout(20*time.Millisecond))
	// When
	err := client.api().do(t.Context(), apiRequest{Path: "/"})
	// Then
	require.ErrorIs(t, err, ErrTimeout)
	require.ErrorIs(t, err, ErrClientTimeout)
	require.ErrorIs(t, err, context.DeadlineExceeded)
	var detail *Error
	require.ErrorAs(t, err, &detail)
	require.Equal(t, 408, detail.StatusCode)
	require.Equal(t, CodeRequestTimeout, detail.Code)
	require.Equal(t, SourceSDK, detail.Source)
}

func TestTransport_whenServerReturnsMalformedShape(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /": jsonHandler(t, map[string]string{"value": "not numeric"})})
	client := newTestClient(t, fake.Server)
	var result struct{ Value int }
	// When
	err := client.api().do(t.Context(), apiRequest{Path: "/", Result: &result})
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	var typeError *json.UnmarshalTypeError
	require.ErrorAs(t, err, &typeError)
}
