// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func imageFixture(id, status string) map[string]any {
	return map[string]any{
		"id": id, "status": status, "class": "linux", "origin": "registry",
		"region_id": "region-a", "region_ids": []string{"region-a", "region-b"},
		"retention": "idle", "anonymous": true, "format_version": 2,
		"format_outdated": false, "platform": false, "default": false, "deprecated": false,
		"created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
	}
}

func TestImageGet_whenMetadataContainsFutureValues(t *testing.T) {
	// Given a future API with optional sizes, timestamps and unknown fields.
	wire := imageFixture("img", "future-status")
	wire["class"], wire["origin"], wire["retention"] = "future-class", "future-origin", "archival"
	wire["bytes"], wire["layer_count"], wire["disk_mib"] = 1024, 7, 81920
	wire["expires_at"], wire["future_field"] = "2026-02-01T00:00:00Z", "ignored"
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /images/img": jsonHandler(t, wire)})
	client := newTestClient(t, fake.Server)

	// When reading through the public boundary.
	image, err := client.Images().Get(t.Context(), "img", nil)

	// Then metadata is mapped and future values retain their original strings.
	require.NoError(t, err)
	require.Equal(t, ImageStatusUnknown, image.Status)
	require.Equal(t, "future-status", image.RawStatus)
	require.Equal(t, ImageClassUnknown, image.Class)
	require.Equal(t, "future-class", image.RawClass)
	require.Equal(t, ImageOriginUnknown, image.Origin)
	require.Equal(t, "future-origin", image.RawOrigin)
	require.Equal(t, ImageRetentionUnknown, image.Retention)
	require.Equal(t, "archival", image.RawRetention)
	require.Equal(t, []string{"region-a", "region-b"}, image.AvailableIn)
	require.Equal(t, "region-a", image.Region)
	require.NotNil(t, image.Bytes)
	require.NotNil(t, image.LayerCount)
	require.NotNil(t, image.DiskGiB)
	require.Equal(t, int64(1024), *image.Bytes)
	require.Equal(t, int64(7), *image.LayerCount)
	require.InDelta(t, 80.0, *image.DiskGiB, 0.000001)
	require.Equal(t, time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), image.CreatedAt)
	require.Equal(t, time.Date(2026, 1, 2, 0, 0, 0, 0, time.UTC), image.UpdatedAt)
	require.Equal(t, time.Date(2026, 2, 1, 0, 0, 0, 0, time.UTC), image.ExpiresAt)
}

func TestImageGet_whenOptionalFieldsAreNull(t *testing.T) {
	// Given explicit null optional fields, distinct from false and zero fields.
	wire := imageFixture("img", "ready")
	for _, field := range []string{"name", "error", "source", "bytes", "disk_mib", "layer_count", "expires_at"} {
		wire[field] = nil
	}
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /images/img": jsonHandler(t, wire)})
	client := newTestClient(t, fake.Server)

	// When reading the observation.
	image, err := client.Images().Get(t.Context(), "img", nil)

	// Then optional fields are absent rather than fabricated.
	require.NoError(t, err)
	require.Empty(t, image.Name)
	require.Empty(t, image.Error)
	require.Nil(t, image.Source)
	require.Nil(t, image.Bytes)
	require.Nil(t, image.DiskGiB)
	require.Nil(t, image.LayerCount)
	require.True(t, image.ExpiresAt.IsZero())
}

func TestImageGet_whenSourceVariantChanges(t *testing.T) {
	for _, tt := range []struct {
		name   string
		wire   map[string]any
		source ImageSource
	}{
		{"registry", map[string]any{"type": "registry", "ref": "alpine:3"}, RegistryImageSource{Ref: "alpine:3"}},
		{"dockerfile", map[string]any{"type": "dockerfile", "context_object": "private", "dockerfile": "FROM alpine"}, DockerfileImageSource{Dockerfile: "FROM alpine"}},
		{"named", map[string]any{"type": "named", "name": "base"}, NamedImageSource{Name: "base"}},
		{"manifest", map[string]any{"type": "manifest", "name": "manifest"}, ManifestImageSource{Name: "manifest"}},
		{"future", map[string]any{"type": "oci_layout", "ref": 42}, UnknownImageSource{RawType: "oci_layout"}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given a known source or a future discriminator with unrelated fields.
			wire := imageFixture("img", "ready")
			wire["source"] = tt.wire
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /images/img": jsonHandler(t, wire)})
			client := newTestClient(t, fake.Server)
			// When reading its public source description.
			image, err := client.Images().Get(t.Context(), "img", nil)
			// Then only known source fields are exposed.
			require.NoError(t, err)
			require.Equal(t, tt.source, image.Source)
		})
	}
}

func TestImageGet_whenRequiredFieldIsMissing(t *testing.T) {
	for field := range imageFixture("img", "ready") {
		t.Run(field, func(t *testing.T) {
			// Given one omitted required field in an otherwise complete response.
			wire := imageFixture("img", "ready")
			delete(wire, field)
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /images/img": jsonHandler(t, wire)})
			client := newTestClient(t, fake.Server)
			// When mapping the response.
			_, err := client.Images().Get(t.Context(), "img", nil)
			// Then the malformed field is named by an SDK bad request.
			require.ErrorIs(t, err, ErrBadRequest)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, SourceSDK, detail.Source)
			require.Contains(t, detail.Message, field)
		})
	}
}

func TestImageGet_whenResponseFieldHasWrongType(t *testing.T) {
	for _, tt := range []struct {
		name, field string
		value       any
	}{
		{"id number", "id", 1},
		{"class number", "class", 1},
		{"origin null", "origin", nil},
		{"status boolean", "status", true},
		{"retention boolean", "retention", false},
		{"region number", "region_id", 1},
		{"anonymous text", "anonymous", "true"},
		{"platform number", "platform", 1},
		{"default text", "default", "false"},
		{"deprecated null", "deprecated", nil},
		{"outdated text", "format_outdated", "false"},
		{"version fraction", "format_version", 1.25},
		{"version boolean", "format_version", true},
		{"regions null", "region_ids", nil},
		{"regions object", "region_ids", map[string]any{}},
		{"region null element", "region_ids", []any{nil}},
		{"region number element", "region_ids", []any{1}},
		{"name number", "name", 1},
		{"error object", "error", map[string]any{}},
		{"bytes text", "bytes", "42"},
		{"bytes fraction", "bytes", 1.25},
		{"bytes overflow", "bytes", json.RawMessage("9223372036854775808")},
		{"layers boolean", "layer_count", true},
		{"disk fraction", "disk_mib", 1.25},
		{"created null", "created_at", nil},
		{"updated text", "updated_at", "yesterday"},
		{"expiry number", "expires_at", 42},
		{"source array", "source", []any{}},
		{"source missing type", "source", map[string]any{"ref": "alpine"}},
		{"source empty type", "source", map[string]any{"type": ""}},
		{"source numeric type", "source", map[string]any{"type": 7}},
		{"registry missing ref", "source", map[string]any{"type": "registry"}},
		{"registry null ref", "source", map[string]any{"type": "registry", "ref": nil}},
		{"dockerfile missing context", "source", map[string]any{"type": "dockerfile", "dockerfile": "FROM alpine"}},
		{"dockerfile wrong text", "source", map[string]any{"type": "dockerfile", "context_object": "ctx", "dockerfile": 1}},
		{"named null name", "source", map[string]any{"type": "named", "name": nil}},
		{"manifest wrong name", "source", map[string]any{"type": "manifest", "name": false}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given a malformed server field.
			wire := imageFixture("img", "ready")
			wire[tt.field] = tt.value
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /images/img": jsonHandler(t, wire)})
			client := newTestClient(t, fake.Server)
			// When reading through the public API.
			_, err := client.Images().Get(t.Context(), "img", nil)
			// Then invalid data never becomes a usable image.
			require.ErrorIs(t, err, ErrBadRequest)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, SourceSDK, detail.Source)
		})
	}
}
