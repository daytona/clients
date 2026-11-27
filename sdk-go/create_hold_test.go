// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestCreateHoldWireBudgets(t *testing.T) {
	for _, tc := range []struct {
		name string
		wait time.Duration
		wire string
	}{
		{"no hold", NoWait, "0"},
		{"whole seconds", 2 * time.Second, "2"},
		{"unbounded wait", WaitForever, "2147483"},
		{"above timer maximum", 2147484 * time.Second, "2147483"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes": jsonHandler(t, map[string]string{"id": "sbx", "status": "running"}),
			})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Create(t.Context(), "", &CreateOptions{Wait: tc.wait, IdempotencyKey: "hold"})
			// Then
			require.NoError(t, err)
			require.JSONEq(t, `{"wait_seconds":`+tc.wire+`}`, string(fake.requests()[0].Body))
		})
	}
}

func TestCreateHoldOutlastsOrdinaryRequestTimeout(t *testing.T) {
	for _, tc := range []struct {
		name    string
		wait    time.Duration
		request time.Duration
	}{
		{"finite hold", time.Second, 20 * time.Millisecond},
		{"unbounded hold", WaitForever, 20 * time.Millisecond},
		{"unbounded request", time.Second, NoTimeout},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given: the server's hold outlasts the ordinary request timeout.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes": func(w http.ResponseWriter, r *http.Request) {
					hold := time.NewTimer(80 * time.Millisecond)
					defer hold.Stop()
					select {
					case <-r.Context().Done():
						return
					case <-hold.C:
						writeJSON(t, w, map[string]string{"id": "sbx", "status": "running"})
					}
				},
			})
			client := newTestClient(t, fake.Server, WithRequestTimeout(tc.request))
			// When
			sandbox, err := client.Create(t.Context(), "", &CreateOptions{Wait: tc.wait})
			// Then
			require.NoError(t, err)
			require.Equal(t, SandboxStateStarted, sandbox.State())
			require.Len(t, fake.requests(), 1)
		})
	}
}
