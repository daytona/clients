// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestCreateFromBuild_whenImageBecomesReady(t *testing.T) {
	// Given a build that needs a readiness hold before sandbox creation.
	var reads atomic.Int32
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /images": jsonHandler(t, map[string]string{"id": "img-build"}),
		"GET /images/img-build": func(w http.ResponseWriter, r *http.Request) {
			if reads.Add(1) == 1 {
				writeJSON(t, w, buildImageFixture("future-building-state"))
				return
			}
			if r.URL.Query().Get("wait") != "ready" {
				t.Error("readiness must be awaited")
			}
			writeJSON(t, w, buildImageFixture("ready"))
		},
		"POST /sandboxes": jsonHandler(t, map[string]string{"id": "sbx-built", "status": "running"}),
	})
	client := newTestClient(t, fake.Server)
	builder := NewImageBuilder("python:3.12-slim").PipInstall("six==1.17.0")
	dockerfile, renderErr := builder.Dockerfile()
	require.NoError(t, renderErr)
	options := &CreateFromBuildOptions{
		CreateOptions: CreateOptions{
			Name: "build-and-boot", Region: "us", CPUArch: CPUArchARM64, Wait: 73 * time.Second,
			Env: map[string]string{"APP": "demo"}, Resources: &Resources{CPU: 2, MemoryGiB: 1},
			User: "app", CPUType: "shared",
		},
		IdleExpiry: 2 * time.Minute, BuildTimeout: 11 * time.Second,
	}

	// When building and booting in one call.
	sandbox, err := client.CreateFromBuild(t.Context(), builder, options)

	// Then placement reaches both legs and the full create wait is preserved.
	require.NoError(t, err)
	require.Equal(t, "sbx-built", sandbox.ID())
	require.Equal(t, SandboxStateStarted, sandbox.State())
	requests := fake.requests()
	require.Len(t, requests, 4)
	require.Equal(t, []string{"/images", "/images/img-build", "/images/img-build", "/sandboxes"},
		[]string{requests[0].Path, requests[1].Path, requests[2].Path, requests[3].Path})
	var build struct {
		Dockerfile string `json:"dockerfile"`
		Region     string `json:"region_id"`
		CPUArch    string `json:"cpu_arch"`
		IdleExpiry int    `json:"idle_expiry_seconds"`
		Name       string `json:"name"`
	}
	require.NoError(t, json.Unmarshal(requests[0].Body, &build))
	require.Equal(t, dockerfile, build.Dockerfile)
	require.Equal(t, "us", build.Region)
	require.Equal(t, "arm64", build.CPUArch)
	require.Equal(t, 120, build.IdleExpiry)
	require.Empty(t, build.Name, "sandbox names must not make the image permanent")
	hold, parseErr := strconv.Atoi(requests[2].Query.Get("timeout_ms"))
	require.NoError(t, parseErr)
	require.Positive(t, hold)
	require.LessOrEqual(t, hold, 11000)
	require.JSONEq(t, `{"image":"img-build","name":"build-and-boot","labels":{"name":"build-and-boot"},
		"region_id":"us","cpu_arch":"arm64","wait_seconds":73,"idempotency_key":"name:build-and-boot",
		"env":{"APP":"demo"},"cpu":2,"memory_mib":1024,"user":"app","cpu_type":"shared"}`, string(requests[3].Body))
	require.Equal(t, "us", requests[3].Header.Get("X-Daytona-Region"))
	require.Equal(t, "name:build-and-boot", requests[3].Header.Get("Idempotency-Key"))
}

func TestCreateFromBuild_whenBuildFails(t *testing.T) {
	for _, status := range []string{"failed", "error"} {
		t.Run(status, func(t *testing.T) {
			// Given a failed build.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /images":          jsonHandler(t, map[string]string{"id": "img-build"}),
				"GET /images/img-build": jsonHandler(t, buildImageFixture(status)),
			})
			client := newTestClient(t, fake.Server)

			// When building a sandbox.
			sandbox, err := client.CreateFromBuild(t.Context(), NewImageBuilder("alpine"), nil)

			// Then the build error identifies the image and no create is submitted.
			require.Nil(t, sandbox)
			var failure *ImageBuildFailedError
			require.ErrorAs(t, err, &failure)
			require.ErrorIs(t, err, ErrImageBuildFailed)
			require.Equal(t, "img-build", failure.ImageID)
			require.Equal(t, "build detail", failure.BuildError)
			require.Len(t, fake.requests(), 2)
		})
	}
}

func TestCreateFromBuild_whenBuildBudgetExpires(t *testing.T) {
	// Given a pending build and a budget that expires before another hold.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /images":          jsonHandler(t, map[string]string{"id": "img-build"}),
		"GET /images/img-build": jsonHandler(t, buildImageFixture("building")),
	})
	client := newTestClient(t, fake.Server)

	// When waiting with an exhausted readiness budget.
	sandbox, err := client.CreateFromBuild(t.Context(), NewImageBuilder("alpine"), &CreateFromBuildOptions{
		BuildTimeout: time.Nanosecond,
	})

	// Then the SDK timeout is returned without creating a sandbox.
	require.Nil(t, sandbox)
	require.ErrorIs(t, err, ErrRequestTimeout)
	var detail *Error
	require.ErrorAs(t, err, &detail)
	require.Equal(t, "SDK", detail.Source)
	require.Len(t, fake.requests(), 2)
}

func TestCreateFromBuild_whenCallerCancelsReadiness(t *testing.T) {
	// Given a build whose readiness hold cancels the caller.
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /images": jsonHandler(t, map[string]string{"id": "img-build"}),
		"GET /images/img-build": func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Query().Get("wait") == "ready" {
				cancel()
				<-r.Context().Done()
				return
			}
			writeJSON(t, w, buildImageFixture("building"))
		},
	})
	client := newTestClient(t, fake.Server)

	// When waiting for readiness.
	sandbox, err := client.CreateFromBuild(ctx, NewImageBuilder("alpine"), nil)

	// Then cancellation is preserved and no sandbox is created.
	require.Nil(t, sandbox)
	require.ErrorIs(t, err, context.Canceled)
	require.Len(t, fake.requests(), 3)
}

func TestCreateFromBuild_whenCreateWaitEndsInTerminalState(t *testing.T) {
	// Given an image already ready, followed by an accepted but failed sandbox.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /images":          jsonHandler(t, map[string]string{"id": "img-build"}),
		"GET /images/img-build": jsonHandler(t, buildImageFixture("ready")),
		"POST /sandboxes": jsonHandler(t, map[string]any{
			"id": "sbx-built", "status": "failed", "wait_cap_seconds": 60,
			"image": map[string]string{"id": "img-build"},
		}),
	})
	client := newTestClient(t, fake.Server)

	// When using default create options.
	sandbox, err := client.CreateFromBuild(t.Context(), NewImageBuilder("alpine"), nil)

	// Then accepted-create semantics return the observed handle, not a build error.
	require.NoError(t, err)
	require.Equal(t, SandboxStateError, sandbox.State())
	require.Equal(t, "img-build", sandbox.Info().Image.ID)
	require.Len(t, fake.requests(), 3)
}
