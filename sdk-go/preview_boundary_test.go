// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

type portsOperation struct {
	route string
	call  func(context.Context, *Ports) error
}

func portsOperations() map[string]portsOperation {
	return map[string]portsOperation{
		"listeners": {"GET /toolbox/port", func(ctx context.Context, p *Ports) error { _, err := p.List(ctx); return err }},
		"in use": {"GET /toolbox/port/8080/in-use", func(ctx context.Context, p *Ports) error {
			_, err := p.IsInUse(ctx, 8080)
			return err
		}},
		"endpoints": {"GET /sandboxes/sbx/ports", func(ctx context.Context, p *Ports) error {
			_, err := p.ListEndpoints(ctx, nil)
			return err
		}},
		"expose": {"PUT /sandboxes/sbx/ports/8080", func(ctx context.Context, p *Ports) error {
			_, err := p.Expose(ctx, 8080, nil)
			return err
		}},
		"preview": {"GET /sandboxes/sbx/ports/8080", func(ctx context.Context, p *Ports) error {
			_, err := p.PreviewLink(ctx, 8080)
			return err
		}},
		"signed": {"POST /sandboxes/sbx/ports/8080/signed-preview-urls", func(ctx context.Context, p *Ports) error {
			_, err := p.SignedPreviewLink(ctx, 8080, nil)
			return err
		}},
		"unexpose": {"DELETE /sandboxes/sbx/ports/8080", func(ctx context.Context, p *Ports) error {
			return p.Unexpose(ctx, 8080)
		}},
		"revoke": {"POST /sandboxes/sbx/signed-preview-urls/revoke", func(ctx context.Context, p *Ports) error {
			return p.ExpireSignedPreviewLinks(ctx)
		}},
	}
}

func TestPortsErrors_whenServerRejectsOperation(t *testing.T) {
	for name, operation := range portsOperations() {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{operation.route: func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusConflict)
				writeJSON(t, w, map[string]string{"message": "unavailable", "code": "CONFLICT", "source": "DAYTONA_API"})
			}})
			sandbox := portsTestSandbox(t, fake)
			// When
			err := operation.call(t.Context(), sandbox.Ports())
			// Then
			require.ErrorIs(t, err, ErrConflict)
			var sdkError *Error
			require.ErrorAs(t, err, &sdkError)
			require.Equal(t, "CONFLICT", sdkError.Code)
			require.Equal(t, "DAYTONA_API", sdkError.Source)
		})
	}
}

func TestPortsCancellation_whenRequestInFlight(t *testing.T) {
	for name, operation := range portsOperations() {
		t.Run(name, func(t *testing.T) {
			// Given
			ctx, cancel := context.WithCancel(t.Context())
			defer cancel()
			fake := newFakeServer(t, map[string]http.HandlerFunc{operation.route: func(_ http.ResponseWriter, r *http.Request) {
				cancel()
				<-r.Context().Done()
			}})
			sandbox := portsTestSandbox(t, fake)
			// When
			err := operation.call(ctx, sandbox.Ports())
			// Then
			require.ErrorIs(t, err, context.Canceled)
			var sdkError *Error
			require.ErrorAs(t, err, &sdkError)
		})
	}
}

func TestPortsClosed_whenClientClosed(t *testing.T) {
	for name, operation := range portsOperations() {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox := portsTestSandbox(t, fake)
			require.NoError(t, sandbox.client.Close())
			// When
			err := operation.call(t.Context(), sandbox.Ports())
			// Then
			require.ErrorIs(t, err, ErrClientClosed)
			require.Empty(t, fake.requests())
		})
	}
}

func TestPortsEndpointMalformed_whenFieldsBreakContract(t *testing.T) {
	for _, name := range []string{"port", "public", "url", "created_at"} {
		for _, value := range []any{nil, []string{}, map[string]string{}} {
			t.Run(name, func(t *testing.T) {
				// Given
				endpoint := previewEndpointFixture()
				endpoint[name] = value
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"PUT /sandboxes/sbx/ports/8080": jsonHandler(t, endpoint),
				})
				sandbox := portsTestSandbox(t, fake)
				// When
				_, err := sandbox.Ports().Expose(t.Context(), 8080, nil)
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				require.ErrorContains(t, err, name)
			})
		}
	}
}

func TestPortsEndpointMalformed_whenTimestampInvalid(t *testing.T) {
	// Given
	endpoint := previewEndpointFixture()
	endpoint["created_at"] = "not-a-time"
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"PUT /sandboxes/sbx/ports/8080": jsonHandler(t, endpoint),
	})
	sandbox := portsTestSandbox(t, fake)
	// When
	_, err := sandbox.Ports().Expose(t.Context(), 8080, nil)
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	require.ErrorContains(t, err, "created_at")
}

func TestPortsPageMalformed_whenEnvelopeBreaksContract(t *testing.T) {
	for _, body := range []string{
		`[]`, `{}`, `{"ports":null}`, `{"ports":[null]}`, `{"ports":[{}]}`,
		`{"ports":[],"next_cursor":12}`, `{"ports":[],"next_cursor":[]}`,
	} {
		t.Run(body, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx/ports": jsonHandler(t, JSONValue(body)),
			})
			sandbox := portsTestSandbox(t, fake)
			// When
			_, err := sandbox.Ports().ListEndpoints(t.Context(), nil)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}
