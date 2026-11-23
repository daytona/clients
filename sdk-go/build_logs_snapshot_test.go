// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestBuildLogsSnapshotIsBoundedText(t *testing.T) {
	tests := []struct {
		name string
		body string
		want string
		fail bool
	}{
		{"state and output", "state: failed\noutput\n", "state: failed\noutput\n", false},
		{"UTF8 replacement", "\xef\xbb\xbfstate: ready\n😀\xff\xe2\x82", "\uFEFFstate: ready\n😀��", false},
		{"exact limit", strings.Repeat("a", buildLogMaxSnapshot), strings.Repeat("a", buildLogMaxSnapshot), false},
		{"overflow", strings.Repeat("a", buildLogMaxSnapshot+1), "", true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Given a chunked one-shot body with no declared length.
			fake := buildLogsFake(t, func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "text/plain")
				w.WriteHeader(http.StatusOK)
				if err := http.NewResponseController(w).Flush(); err != nil {
					t.Errorf("flush: %v", err)
					return
				}
				if _, err := io.WriteString(w, tt.body); err != nil {
					// An oversized body may be closed before all bytes are written.
					if !tt.fail {
						t.Errorf("write snapshot: %v", err)
					}
				}
			})
			client := newTestClient(t, fake.Server, WithMaxResponseBytes(32), WithRequestTimeout(NoTimeout))
			// When reading retained logs.
			snapshot, err := (&Sandbox{client: client, id: "sbx"}).BuildLogs(t.Context())
			// Then the fixed snapshot cap applies independently of the JSON cap.
			if tt.fail {
				var sdkError *Error
				require.ErrorAs(t, err, &sdkError)
				require.Equal(t, "BUILD_LOG_PROTOCOL_ERROR", sdkError.Code)
				require.Nil(t, snapshot)
			} else {
				require.NoError(t, err)
				require.Equal(t, tt.want, snapshot.Text)
			}
			requests := fake.requests()
			require.Len(t, requests, 2)
			require.Equal(t, "false", requests[1].Query.Get("follow"))
			require.Equal(t, "text", requests[1].Query.Get("format"))
		})
	}
}

func TestBuildLogsEntrypointUsesOrdinaryRead(t *testing.T) {
	// Given retained entrypoint logs at the sandbox route.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/{id}/logs": func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "text/plain")
			if _, err := io.WriteString(w, "entrypoint\n"); err != nil {
				t.Errorf("write entrypoint: %v", err)
			}
		},
	})
	client := newTestClient(t, fake.Server)
	// When reading entrypoint logs.
	text, err := (&Sandbox{client: client, id: "sbx"}).EntrypointLogs(t.Context())
	// Then one ordinary API request returns text.
	require.NoError(t, err)
	require.Equal(t, "entrypoint\n", text)
	require.Len(t, fake.requests(), 1)
	require.Equal(t, "/sandboxes/sbx/logs", fake.requests()[0].Path)
}

func TestBuildLogsDiscoveryRejectsMalformedJSON(t *testing.T) {
	for _, body := range []string{
		`{"url":7}`, `{"url":null}`, `[]`, "\uFEFF{}", `{"url":"` + "\xff" + `"}`,
		strings.Repeat(" ", buildLogMaxEvent+1),
	} {
		t.Run(body[:min(len(body), 20)], func(t *testing.T) {
			// Given malformed or oversized discovery.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx/build-logs-url": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					if _, err := io.WriteString(w, body); err != nil {
						t.Errorf("write discovery: %v", err)
					}
				},
			})
			client := newTestClient(t, fake.Server)
			// When requesting a snapshot.
			_, err := (&Sandbox{client: client, id: "sbx"}).BuildLogs(t.Context())
			// Then invalid discovery never reaches another route.
			var sdkError *Error
			require.ErrorAs(t, err, &sdkError)
			require.Equal(t, "BUILD_LOG_PROTOCOL_ERROR", sdkError.Code)
			require.Len(t, fake.requests(), 1)
		})
	}
}
