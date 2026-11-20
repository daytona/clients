// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestErrors_whenResponseCarriesTraceHeaders(t *testing.T) {
	for _, tt := range []struct {
		name, mediaType, body, headerTrace, wantTrace, wantRequest string
	}{
		{"non-JSON gateway", "text/plain", "upstream unavailable", "header-trace", "header-trace", ""},
		{"JSON without IDs", "application/json", `{"message":"unavailable"}`, "header-trace", "header-trace", ""},
		{"body IDs win", "application/json", `{"message":"unavailable","trace_id":"body-trace","requestId":"body-request"}`, "header-trace", "body-trace", "body-request"},
		{"unsupported headers", "text/plain", "upstream unavailable", "", "", ""},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given a gateway response with distinct contract and unsupported headers.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /images/missing": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", tt.mediaType)
					w.Header().Set("X-Daytona-Trace-Id", tt.headerTrace)
					if tt.headerTrace == "" {
						w.Header().Set("X-Trace-ID", "unsupported-trace")
						w.Header().Set("X-Request-ID", "unsupported-request")
					}
					w.WriteHeader(http.StatusBadGateway)
					if _, err := w.Write([]byte(tt.body)); err != nil {
						t.Errorf("write gateway body: %v", err)
					}
				},
			})
			client := newTestClient(t, fake.Server)

			// When a public API call receives the response.
			_, err := client.Images().Get(t.Context(), "missing", nil)

			// Then diagnostics use the body first, falling back only to the contract header.
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, tt.wantTrace, detail.TraceID)
			require.Equal(t, tt.wantRequest, detail.RequestID)
		})
	}
}
