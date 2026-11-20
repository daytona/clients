// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestImagesBuildSubmitsSourceWhenBuilderHasNoSteps(t *testing.T) {
	// Given a bare registry reference and a pending image.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /images":          jsonHandler(t, map[string]string{"id": "img-build"}),
		"GET /images/img-build": jsonHandler(t, buildImageFixture("pending")),
	})
	client := newTestClient(t, fake.Server)

	// When submitting without waiting.
	image, err := client.Images().Build(t.Context(), NewImageBuilder("python:3.12"), &BuildImageOptions{
		Name: "reusable", Region: "us", CPUArch: ImageCPUArchARM64,
	})

	// Then the registry source is not silently converted into a Dockerfile build.
	require.NoError(t, err)
	require.Equal(t, ImageStatusPending, image.Status)
	require.Len(t, fake.requests(), 2)
	require.JSONEq(t, `{"source":{"type":"registry","ref":"python:3.12"},"name":"reusable","region_id":"us","cpu_arch":"arm64"}`,
		string(fake.requests()[0].Body))
}

func TestImagesBuildSubmitsDockerfileWhenBuilderHasSteps(t *testing.T) {
	// Given a builder with a real installation step.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /images":          jsonHandler(t, map[string]string{"id": "img-build"}),
		"GET /images/img-build": jsonHandler(t, buildImageFixture("building")),
	})
	client := newTestClient(t, fake.Server)
	builder := NewImageBuilder("python:3.12").PipInstall("pandas")
	expected, err := builder.Dockerfile()
	require.NoError(t, err)

	// When building with the maximum idle retention.
	image, err := client.Images().Build(t.Context(), builder, &BuildImageOptions{IdleExpiry: WaitForever, Region: " LOCAL "})

	// Then the request uses the rendered Dockerfile, not a registry import.
	require.NoError(t, err)
	require.Equal(t, ImageStatusBuilding, image.Status)
	var body struct {
		Dockerfile string               `json:"dockerfile"`
		IdleExpiry int64                `json:"idle_expiry_seconds"`
		Region     string               `json:"region_id"`
		Source     *imageRegistrySource `json:"source"`
	}
	require.NoError(t, json.Unmarshal(fake.requests()[0].Body, &body))
	require.Equal(t, expected, body.Dockerfile)
	require.EqualValues(t, -1, body.IdleExpiry)
	require.Empty(t, body.Region)
	require.Nil(t, body.Source)
}

func TestImagesBuildRefusesInvalidOptionsBeforeSubmission(t *testing.T) {
	tests := []struct {
		name    string
		options BuildImageOptions
	}{
		{"blank name", BuildImageOptions{Name: "  "}},
		{"invalid region", BuildImageOptions{Region: " region"}},
		{"invalid architecture", BuildImageOptions{CPUArch: "other"}},
		{"negative expiry", BuildImageOptions{IdleExpiry: -time.Second}},
		{"fractional expiry", BuildImageOptions{IdleExpiry: time.Millisecond}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			// Given invalid build options.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When building.
			_, err := client.Images().Build(t.Context(), NewImageBuilder("alpine"), &test.options)
			// Then invalid input is refused locally.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestImagesBuildRefusesNilBuilderWithoutPanic(t *testing.T) {
	// Given an omitted builder.
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	// When building.
	_, err := client.Images().Build(t.Context(), nil, nil)
	// Then validation fails before any request.
	require.ErrorIs(t, err, ErrBadRequest)
	require.Empty(t, fake.requests())
}

func TestImagesBuildAndWaitRaisesRecordedFailure(t *testing.T) {
	// Given a server which records a build failure.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /images":          jsonHandler(t, map[string]string{"id": "img-build"}),
		"GET /images/img-build": jsonHandler(t, buildImageFixture("failed")),
	})
	client := newTestClient(t, fake.Server)
	// When building to readiness.
	_, err := client.Images().BuildAndWait(t.Context(), NewImageBuilder("alpine"), nil)
	// Then callers can identify the failed image and its build detail.
	var failure *ImageBuildFailedError
	require.ErrorAs(t, err, &failure)
	require.Equal(t, "img-build", failure.ImageID)
	require.Equal(t, "build detail", failure.BuildError)
	require.ErrorIs(t, err, ErrImageBuildFailed)
}

func TestImagesBuildAndWaitHonorsCancellation(t *testing.T) {
	// Given a cancelled caller context.
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	// When building.
	_, err := client.Images().BuildAndWait(ctx, NewImageBuilder("alpine"), nil)
	// Then cancellation remains reachable through the SDK error hierarchy.
	require.ErrorIs(t, err, context.Canceled)
	var sdkError *Error
	require.ErrorAs(t, err, &sdkError)
}

func buildImageFixture(status string) map[string]any {
	return map[string]any{
		"id": "img-build", "class": "linux", "anonymous": true, "status": status,
		"created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
		"origin": "registry", "region_id": "us", "region_ids": []string{"us"},
		"retention": "idle", "format_version": 2, "format_outdated": false,
		"platform": false, "default": false, "deprecated": false, "error": "build detail",
	}
}
