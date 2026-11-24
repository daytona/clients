// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"fmt"
	"net/http"
	"strconv"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestPreviewEndpointShowsConnectionDetails_whenFormatted(t *testing.T) {
	for _, public := range []bool{false, true} {
		t.Run(strconv.FormatBool(public), func(t *testing.T) {
			// Given a registered endpoint URL without a preview credential.
			endpoint := PreviewEndpoint{Port: 8080, Public: public, URL: "https://8080-sbx.preview.invalid/"}
			for _, format := range []string{"%v", "%+v", "%#v"} {
				// When printing the endpoint for diagnostics.
				shown := fmt.Sprintf(format, endpoint)
				// Then connection details remain visible in every standard inspection form.
				require.Contains(t, shown, "Port:8080")
				require.Contains(t, shown, "Public:"+strconv.FormatBool(public))
				require.Contains(t, shown, endpoint.URL)
			}
		})
	}
}

func TestPortsExpose_whenVisibilitySelected(t *testing.T) {
	for _, public := range []bool{false, true} {
		t.Run(strconv.FormatBool(public), func(t *testing.T) {
			// Given
			endpoint := previewEndpointFixture()
			endpoint["public"] = public
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"PUT /sandboxes/sbx/ports/8080": jsonHandler(t, endpoint),
			})
			sandbox := portsTestSandbox(t, fake)
			var opts *ExposePortOptions
			if public {
				opts = &ExposePortOptions{Public: true}
			}
			// When
			result, err := sandbox.Ports().Expose(t.Context(), 8080, opts)
			// Then
			require.NoError(t, err)
			require.Equal(t, public, result.Public)
			require.Equal(t, 8080, result.Port)
			require.Equal(t, "https://preview.invalid", result.URL)
			require.Equal(t, time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), result.CreatedAt)
			request := fake.requests()[0]
			require.JSONEq(t, `{"public":`+strconv.FormatBool(public)+`}`, string(request.Body))
			require.Equal(t, "application/json", request.Header.Get("Content-Type"))
			require.Equal(t, "Bearer test-key", request.Header.Get("Authorization"))
		})
	}
}

func TestPortsListEndpoints_whenOnePageRequested(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx/ports": jsonHandler(t, map[string]any{
			"ports": []any{previewEndpointFixture()}, "next_cursor": "next",
		}),
	})
	sandbox := portsTestSandbox(t, fake)
	// When
	page, err := sandbox.Ports().ListEndpoints(t.Context(), &ListEndpointsOptions{Cursor: "a+b /", Limit: 2})
	// Then
	require.NoError(t, err)
	require.Len(t, page.Items, 1)
	require.Equal(t, 8080, page.Items[0].Port)
	require.Equal(t, "next", page.NextCursor)
	require.Len(t, fake.requests(), 1)
	require.Equal(t, "a+b /", fake.requests()[0].Query.Get("cursor"))
	require.Equal(t, "2", fake.requests()[0].Query.Get("limit"))
}

func TestPortsListEndpointsAll_whenCursorCycles(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx/ports": func(w http.ResponseWriter, r *http.Request) {
			next := "b"
			if r.URL.Query().Get("cursor") == "b" {
				next = "a"
			}
			writeJSON(t, w, map[string]any{"ports": []any{previewEndpointFixture()}, "next_cursor": next})
		},
	})
	sandbox := portsTestSandbox(t, fake)
	options := &ListEndpointsOptions{Cursor: "a", Limit: 1}
	iterator := sandbox.Ports().ListEndpointsAll(t.Context(), options)
	options.Cursor, options.Limit = "changed", 200
	// When
	var items []PreviewEndpoint
	for item, err := range iterator {
		require.NoError(t, err)
		items = append(items, item)
	}
	// Then
	require.Len(t, items, 2)
	require.Len(t, fake.requests(), 2)
	require.Equal(t, "a", fake.requests()[0].Query.Get("cursor"))
	require.Equal(t, "1", fake.requests()[1].Query.Get("limit"))
}

func TestPortsListEndpointsAll_whenConsumerBreaks(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx/ports": jsonHandler(t, map[string]any{
			"ports": []any{previewEndpointFixture()}, "next_cursor": "more",
		}),
	})
	sandbox := portsTestSandbox(t, fake)
	// When
	for _, err := range sandbox.Ports().ListEndpointsAll(t.Context(), nil) {
		require.NoError(t, err)
		break
	}
	// Then
	require.Len(t, fake.requests(), 1)
}

func TestPortsListEndpoints_whenInvalidPageOptions(t *testing.T) {
	for _, opts := range []ListEndpointsOptions{{Limit: -1}, {Limit: 201}, {Cursor: "\xff"}} {
		t.Run(strconv.Itoa(opts.Limit)+opts.Cursor, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox := portsTestSandbox(t, fake)
			// When
			_, err := sandbox.Ports().ListEndpoints(t.Context(), &opts)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestPortsUnexpose_whenReservedPort(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"DELETE /sandboxes/sbx/ports/2280": func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) },
	})
	sandbox := portsTestSandbox(t, fake)
	// When
	err := sandbox.Ports().Unexpose(t.Context(), 2280)
	// Then
	require.NoError(t, err)
	require.Empty(t, fake.requests()[0].Body)
}

func TestPortsExpireSignedPreviewLinks_whenCalled(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/sbx/signed-preview-urls/revoke": func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusNoContent)
		},
	})
	sandbox := portsTestSandbox(t, fake)
	// When
	err := sandbox.Ports().ExpireSignedPreviewLinks(t.Context())
	// Then
	require.NoError(t, err)
	require.Empty(t, fake.requests()[0].Body)
}
