// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func volumeFixture() map[string]any {
	return map[string]any{
		"id": "vol_1", "name": "data", "type": "disk", "status": "ready",
		"created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
	}
}

func TestVolumesCreate_whenOptionsSelectStorage(t *testing.T) {
	for _, tt := range []struct {
		name string
		opts *CreateVolumeOptions
		body string
	}{
		{"default disk", nil, `{"name":"data"}`},
		{"explicit disk", &CreateVolumeOptions{Type: VolumeTypeDisk, SizeGiB: 8}, `{"name":"data","type":"disk","size_gib":8}`},
		{"explicit bucket", &CreateVolumeOptions{Type: VolumeTypeBucket}, `{"name":"data","type":"bucket"}`},
		{"maximum", &CreateVolumeOptions{SizeGiB: 2147483647}, `{"name":"data","size_gib":2147483647}`},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /volumes": jsonHandler(t, volumeFixture())})
			client := newTestClient(t, fake.Server)
			// When
			got, err := client.Volumes().Create(t.Context(), "data", tt.opts)
			// Then
			require.NoError(t, err)
			require.Equal(t, "vol_1", got.ID)
			require.Equal(t, VolumeStatusReady, got.Status)
			requests := fake.requests()
			require.Len(t, requests, 1)
			require.JSONEq(t, tt.body, string(requests[0].Body))
		})
	}
}

func TestVolumesGet_whenLookupMisses(t *testing.T) {
	for _, tt := range []struct {
		name   string
		opts   *GetVolumeOptions
		status int
		want   error
		count  int
	}{
		{"create", &GetVolumeOptions{Create: true}, 404, nil, 2},
		{"read only", nil, 404, ErrNotFound, 1},
		{"id only", &GetVolumeOptions{Create: true, IDOnly: true}, 404, ErrNotFound, 1},
		{"forbidden", &GetVolumeOptions{Create: true}, 403, ErrForbidden, 1},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /volumes/{ref}": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(tt.status)
					writeJSON(t, w, map[string]string{"code": "VOLUME_NOT_FOUND", "source": "API", "message": "absent"})
				},
				"POST /volumes": jsonHandler(t, volumeFixture()),
			})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Volumes().Get(t.Context(), "data/name", tt.opts)
			// Then
			if tt.want == nil {
				require.NoError(t, err)
			} else {
				require.ErrorIs(t, err, tt.want)
				var sdkErr *Error
				require.ErrorAs(t, err, &sdkErr)
				require.Equal(t, "VOLUME_NOT_FOUND", sdkErr.Code)
			}
			requests := fake.requests()
			require.Len(t, requests, tt.count)
			require.Equal(t, "/volumes/data%2Fname", requests[0].Path)
			if tt.opts != nil && tt.opts.IDOnly {
				require.Equal(t, "true", requests[0].Query.Get("id_only"))
			}
			if tt.count == 2 {
				require.JSONEq(t, `{"name":"data/name"}`, string(requests[1].Body))
			}
		})
	}
}

func TestVolumesDelete_whenIDOnly(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"DELETE /volumes/{ref}": func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) },
	})
	client := newTestClient(t, fake.Server)
	// When
	err := client.Volumes().Delete(t.Context(), "vol/name", &DeleteVolumeOptions{IDOnly: true})
	// Then
	require.NoError(t, err)
	requests := fake.requests()
	require.Len(t, requests, 1)
	require.Equal(t, "/volumes/vol%2Fname", requests[0].Path)
	require.Equal(t, "true", requests[0].Query.Get("id_only"))
}

func TestVolumesRejectInput_whenInvalid(t *testing.T) {
	for _, tt := range []struct {
		name string
		call func(context.Context, *Volumes) error
	}{
		{"blank name", func(ctx context.Context, v *Volumes) error { _, err := v.Create(ctx, " \n", nil); return err }},
		{"invalid utf8", func(ctx context.Context, v *Volumes) error {
			_, err := v.Create(ctx, string([]byte{255}), nil)
			return err
		}},
		{"negative size", func(ctx context.Context, v *Volumes) error {
			_, err := v.Create(ctx, "v", &CreateVolumeOptions{SizeGiB: -1})
			return err
		}},
		{"large size", func(ctx context.Context, v *Volumes) error {
			_, err := v.Create(ctx, "v", &CreateVolumeOptions{SizeGiB: 2147483648})
			return err
		}},
		{"unknown type", func(ctx context.Context, v *Volumes) error {
			_, err := v.Create(ctx, "v", &CreateVolumeOptions{Type: VolumeTypeUnknown})
			return err
		}},
		{"future type", func(ctx context.Context, v *Volumes) error {
			_, err := v.Create(ctx, "v", &CreateVolumeOptions{Type: "future"})
			return err
		}},
		{"get dot", func(ctx context.Context, v *Volumes) error { _, err := v.Get(ctx, ".", nil); return err }},
		{"delete blank", func(ctx context.Context, v *Volumes) error { return v.Delete(ctx, "", nil) }},
		{"negative limit", func(ctx context.Context, v *Volumes) error {
			_, err := v.List(ctx, &ListVolumesOptions{Limit: -1})
			return err
		}},
		{"large limit", func(ctx context.Context, v *Volumes) error {
			_, err := v.List(ctx, &ListVolumesOptions{Limit: 201})
			return err
		}},
		{"invalid cursor", func(ctx context.Context, v *Volumes) error {
			_, err := v.List(ctx, &ListVolumesOptions{Cursor: string([]byte{255})})
			return err
		}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When
			err := tt.call(t.Context(), client.Volumes())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			var sdkErr *Error
			require.ErrorAs(t, err, &sdkErr)
			require.Equal(t, "SDK", sdkErr.Source)
			require.Empty(t, fake.requests())
		})
	}
}

func TestVolumesGet_whenMetadataContainsUnknownEnums(t *testing.T) {
	// Given
	fixture := volumeFixture()
	fixture["type"], fixture["status"] = "future_type", "future_state"
	fixture["options"] = map[string]any{"nested": []any{true, nil, map[string]string{"mode": "read"}}}
	fixture["size_gib"], fixture["used_bytes"] = nil, 0
	fixture["used_bytes_at"] = "2026-01-02T01:02:03.123Z"
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /volumes/v": jsonHandler(t, fixture)})
	client := newTestClient(t, fake.Server)
	// When
	got, err := client.Volumes().Get(t.Context(), "v", nil)
	// Then
	require.NoError(t, err)
	require.Equal(t, VolumeTypeUnknown, got.Type)
	require.Equal(t, "future_type", got.RawType)
	require.Equal(t, VolumeStatusUnknown, got.Status)
	require.Equal(t, "future_state", got.RawStatus)
	require.Nil(t, got.SizeGiB)
	require.Equal(t, int64(0), *got.UsedBytes)
	require.Equal(t, 123000000, got.UsedBytesAt.Nanosecond())
	require.JSONEq(t, `[true,null,{"mode":"read"}]`, string(got.Options["nested"]))
	require.Equal(t, time.UTC, got.CreatedAt.Location())
	require.NotContains(t, fmt.Sprintf("%v %#v", client.Volumes(), client.Volumes()), "test-key")
}
