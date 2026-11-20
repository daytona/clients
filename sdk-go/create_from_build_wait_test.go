// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestCreateFromBuild_whenBuildWaitIsUnbounded(t *testing.T) {
	for _, budget := range []time.Duration{0, WaitForever, 30 * 24 * time.Hour} {
		t.Run(budget.String(), func(t *testing.T) {
			// Given a build requiring a hold without a total SDK readiness deadline.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /images": jsonHandler(t, map[string]string{"id": "img-build"}),
				"GET /images/img-build": func(w http.ResponseWriter, r *http.Request) {
					status := "building"
					if r.URL.Query().Get("wait") == "ready" {
						status = "ready"
					}
					writeJSON(t, w, buildImageFixture(status))
				},
				"POST /sandboxes": jsonHandler(t, map[string]string{"id": "sbx-built", "status": "pending"}),
			})
			client := newTestClient(t, fake.Server)

			// When the caller skips only the sandbox wait.
			sandbox, err := client.CreateFromBuild(t.Context(), NewImageBuilder("alpine"), &CreateFromBuildOptions{
				CreateOptions: CreateOptions{Wait: NoWait}, BuildTimeout: budget, IdleExpiry: WaitForever,
			})

			// Then image readiness is still awaited with bounded individual holds.
			require.NoError(t, err)
			require.Equal(t, SandboxStateCreating, sandbox.State())
			requests := fake.requests()
			require.Len(t, requests, 4)
			require.Equal(t, "60000", requests[2].Query.Get("timeout_ms"))
			require.Contains(t, string(requests[3].Body), `"wait_seconds":0`)
		})
	}
}

func TestCreateFromBuild_whenReadinessReturnsServerError(t *testing.T) {
	for _, status := range []int{http.StatusForbidden, http.StatusRequestTimeout} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			// Given a build accepted before a readiness read fails on the server.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /images": jsonHandler(t, map[string]string{"id": "img-build"}),
				"GET /images/img-build": func(w http.ResponseWriter, r *http.Request) {
					if r.URL.Query().Get("wait") == "ready" {
						w.Header().Set("Content-Type", "application/json")
						w.WriteHeader(status)
						writeJSON(t, w, map[string]string{"code": "BUILD_READ_ERROR", "source": "API", "message": "read failed"})
						return
					}
					writeJSON(t, w, buildImageFixture("building"))
				},
			})
			client := newTestClient(t, fake.Server)

			// When a finite build budget is in effect.
			sandbox, err := client.CreateFromBuild(t.Context(), NewImageBuilder("alpine"), &CreateFromBuildOptions{
				BuildTimeout: time.Minute,
			})

			// Then server errors retain their source rather than becoming SDK timeouts.
			require.Nil(t, sandbox)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, status, detail.StatusCode)
			require.Equal(t, "API", detail.Source)
			require.Equal(t, "BUILD_READ_ERROR", detail.Code)
			require.Len(t, fake.requests(), 3)
		})
	}
}

func TestCreateFromBuild_whenClientIsClosed(t *testing.T) {
	// Given a closed client.
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	require.NoError(t, client.Close())

	// When creating from a valid builder.
	_, err := client.CreateFromBuild(t.Context(), NewImageBuilder("alpine"), nil)

	// Then no billed request is sent.
	require.ErrorIs(t, err, ErrClientClosed)
	require.Empty(t, fake.requests())
}
