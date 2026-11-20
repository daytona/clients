// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"strconv"
	"testing"

	"github.com/stretchr/testify/require"
)

func portsTestSandbox(t *testing.T, fake *fakeServer) *Sandbox {
	t.Helper()
	sandbox, err := newTestClient(t, fake.Server).newSandbox(sandboxInit{
		Info: SandboxInfo{ID: "sbx"}, Options: GetSandboxOptions{
			ToolboxURL: fake.Server.URL + "/toolbox", AccessToken: "caller-token",
		},
	})
	require.NoError(t, err)
	return sandbox
}

func previewEndpointFixture() map[string]any {
	return map[string]any{
		"port": 8080, "public": false, "url": "https://preview.invalid",
		"created_at": "2026-01-01T00:00:00Z", "unknown": "tolerated",
	}
}

func TestPortsList_whenToolboxReportsInt32Values(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /toolbox/port": jsonHandler(t, map[string]any{"ports": []int{-2147483648, 8080, 2147483647}}),
	})
	sandbox := portsTestSandbox(t, fake)
	// When
	page, err := sandbox.Ports().List(t.Context())
	// Then
	require.NoError(t, err)
	require.Equal(t, []int{-2147483648, 8080, 2147483647}, page.Items)
	require.Empty(t, page.NextCursor)
	request := fake.requests()[0]
	require.Equal(t, "Bearer caller-token", request.Header.Get("Authorization"))
	require.Equal(t, "sdk-go", request.Header.Get("X-Daytona-Source"))
	require.Equal(t, http.MethodGet, request.Method)
	require.Empty(t, request.Query)
	require.Empty(t, request.Body)
}

func TestPortsIsInUse_whenListenerExists(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /toolbox/port/8080/in-use": jsonHandler(t, map[string]bool{"isInUse": true}),
	})
	sandbox := portsTestSandbox(t, fake)
	// When
	inUse, err := sandbox.Ports().IsInUse(t.Context(), 8080)
	// Then
	require.NoError(t, err)
	require.True(t, inUse)
	require.Equal(t, "Bearer caller-token", fake.requests()[0].Header.Get("Authorization"))
}

func TestPortsToolboxMalformed_whenFieldsBreakContract(t *testing.T) {
	for _, test := range []struct {
		name string
		body JSONValue
		path string
		call func(context.Context, *Ports) error
	}{
		{"list missing", JSONValue(`{}`), "/port", func(ctx context.Context, p *Ports) error { _, err := p.List(ctx); return err }},
		{"list null", JSONValue(`{"ports":null}`), "/port", func(ctx context.Context, p *Ports) error { _, err := p.List(ctx); return err }},
		{"list fraction", JSONValue(`{"ports":[1.5]}`), "/port", func(ctx context.Context, p *Ports) error { _, err := p.List(ctx); return err }},
		{"list overflow", JSONValue(`{"ports":[2147483648]}`), "/port", func(ctx context.Context, p *Ports) error { _, err := p.List(ctx); return err }},
		{"list null item", JSONValue(`{"ports":[null]}`), "/port", func(ctx context.Context, p *Ports) error { _, err := p.List(ctx); return err }},
		{"in use missing", JSONValue(`{}`), "/port/8080/in-use", func(ctx context.Context, p *Ports) error { _, err := p.IsInUse(ctx, 8080); return err }},
		{"in use type", JSONValue(`{"isInUse":1}`), "/port/8080/in-use", func(ctx context.Context, p *Ports) error { _, err := p.IsInUse(ctx, 8080); return err }},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /toolbox" + test.path: jsonHandler(t, test.body)})
			sandbox := portsTestSandbox(t, fake)
			// When
			err := test.call(t.Context(), sandbox.Ports())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			var sdkError *Error
			require.ErrorAs(t, err, &sdkError)
			require.Equal(t, "SDK", sdkError.Source)
		})
	}
}

func TestPortsValidation_whenPortOutsideRange(t *testing.T) {
	for _, port := range []int{-1, 0, 65536} {
		for name, call := range map[string]func(context.Context, *Ports) error{
			"in use":   func(ctx context.Context, p *Ports) error { _, err := p.IsInUse(ctx, port); return err },
			"expose":   func(ctx context.Context, p *Ports) error { _, err := p.Expose(ctx, port, nil); return err },
			"preview":  func(ctx context.Context, p *Ports) error { _, err := p.PreviewLink(ctx, port); return err },
			"signed":   func(ctx context.Context, p *Ports) error { _, err := p.SignedPreviewLink(ctx, port, 0); return err },
			"unexpose": func(ctx context.Context, p *Ports) error { return p.Unexpose(ctx, port) },
		} {
			t.Run(name+"/"+strconv.Itoa(port), func(t *testing.T) {
				// Given
				fake := newFakeServer(t, nil)
				sandbox := portsTestSandbox(t, fake)
				// When
				err := call(t.Context(), sandbox.Ports())
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				require.Empty(t, fake.requests())
			})
		}
	}
}

func TestPortsValidation_whenPreviewPortReserved(t *testing.T) {
	for _, port := range []int{2280, 2281, 22220} {
		for name, call := range map[string]func(context.Context, *Ports) error{
			"expose":  func(ctx context.Context, p *Ports) error { _, err := p.Expose(ctx, port, nil); return err },
			"preview": func(ctx context.Context, p *Ports) error { _, err := p.PreviewLink(ctx, port); return err },
			"signed":  func(ctx context.Context, p *Ports) error { _, err := p.SignedPreviewLink(ctx, port, 0); return err },
		} {
			t.Run(name+"/"+strconv.Itoa(port), func(t *testing.T) {
				// Given
				fake := newFakeServer(t, nil)
				sandbox := portsTestSandbox(t, fake)
				// When
				err := call(t.Context(), sandbox.Ports())
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				require.Empty(t, fake.requests())
			})
		}
	}
}
