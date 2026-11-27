// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestPortsLinkMalformed_whenFieldsBreakContract(t *testing.T) {
	for name, operation := range map[string]portsOperation{
		"route token": {"POST /sandboxes/sbx/ports/8080/preview-url", func(ctx context.Context, p *Ports) error {
			_, err := p.PreviewLink(ctx, 8080)
			return err
		}},
		"signed token": portsOperations()["signed"],
	} {
		for _, body := range []string{
			`[]`, `{}`, `{"url":null,"token":"t"}`, `{"url":"u","token":null}`,
			`{"url":"u","token":1}`, `{"url":"u","token":"t","expires_at":false}`,
			`{"url":"u","token":"t"}`, `{"url":"u","token":"t","expires_at":"invalid"}`,
		} {
			// A preview URL does not read expires_at; signed preview URLs require it.
			if name == "route token" && (strings.Contains(body, "expires_at") || body == `{"url":"u","token":"t"}`) {
				continue
			}
			t.Run(name+"/"+body, func(t *testing.T) {
				// Given
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"GET /sandboxes/sbx/ports/8080": jsonHandler(t, previewEndpointFixture()),
					operation.route:                 jsonHandler(t, JSONValue(body)),
				})
				sandbox := portsTestSandbox(t, fake)
				// When
				err := operation.call(t.Context(), sandbox.Ports())
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
			})
		}
	}
}

func TestPortsPreviewLink_whenMintMalformedRetainsRouteToken(t *testing.T) {
	for _, body := range []string{
		`{}`, `{"access_token":""}`,
		`{"access_token":"minted","token_type":"Bearer","operations":[],"expires_at":"bad"}`,
		`{"access_token":"minted","token_type":"Bearer","operations":[],"expires_at":"2027-01-01T00:00:00Z","ports":[null]}`,
		`{"access_token":"minted","token_type":"Bearer","operations":[null],"expires_at":"2027-01-01T00:00:00Z"}`,
	} {
		t.Run(body, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx/ports/8080":              jsonHandler(t, previewEndpointFixture()),
				"POST /sandboxes/sbx/ports/8080/preview-url": jsonHandler(t, map[string]string{"url": "https://route.invalid", "token": "route"}),
				"POST /sandboxes/sbx/access-token":           jsonHandler(t, JSONValue(body)),
			})
			sandbox := portsTestSandbox(t, fake)
			sandbox.auth.mintable = true
			// When
			link, err := sandbox.Ports().PreviewLink(t.Context(), 8080)
			// Then
			require.NoError(t, err)
			require.Equal(t, "route", link.Token)
		})
	}
}

func TestPortsPreviewLink_whenClientClosesDuringMint(t *testing.T) {
	// Given
	var client *Client
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx/ports/8080":              jsonHandler(t, previewEndpointFixture()),
		"POST /sandboxes/sbx/ports/8080/preview-url": jsonHandler(t, map[string]string{"url": "https://route.invalid", "token": "route"}),
		"POST /sandboxes/sbx/access-token": func(_ http.ResponseWriter, r *http.Request) {
			if closeErr := client.Close(); closeErr != nil {
				t.Errorf("close: %v", closeErr)
			}
			<-r.Context().Done()
		},
	})
	sandbox := portsTestSandbox(t, fake)
	client = sandbox.client
	sandbox.auth.mintable = true
	// When
	_, err := sandbox.Ports().PreviewLink(t.Context(), 8080)
	// Then
	require.ErrorIs(t, err, ErrClientClosed)
}

func TestPortsPreviewLink_whenMintCancelledPropagatesCancellation(t *testing.T) {
	// Given
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx/ports/8080":              jsonHandler(t, previewEndpointFixture()),
		"POST /sandboxes/sbx/ports/8080/preview-url": jsonHandler(t, map[string]string{"url": "https://route.invalid", "token": "route"}),
		"POST /sandboxes/sbx/access-token": func(_ http.ResponseWriter, r *http.Request) {
			cancel()
			<-r.Context().Done()
		},
	})
	sandbox := portsTestSandbox(t, fake)
	sandbox.auth.mintable = true
	// When
	_, err := sandbox.Ports().PreviewLink(ctx, 8080)
	// Then
	require.ErrorIs(t, err, context.Canceled)
}

func TestPortsListEndpointsAll_whenCancelledBeforeIteration(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	sandbox := portsTestSandbox(t, fake)
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	// When
	var failures []error
	for _, err := range sandbox.Ports().ListEndpointsAll(ctx, nil) {
		failures = append(failures, err)
	}
	// Then
	require.Len(t, failures, 1)
	require.ErrorIs(t, failures[0], context.Canceled)
	require.Empty(t, fake.requests())
}

func TestPortsListEndpointsAll_whenFetchFailsYieldsErrorOnce(t *testing.T) {
	// Given
	pauses := recordPageRetryPauses(t)
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx/ports": func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(503) },
	})
	sandbox := portsTestSandbox(t, fake)
	// When
	var failures []error
	for _, err := range sandbox.Ports().ListEndpointsAll(t.Context(), nil) {
		failures = append(failures, err)
	}
	// Then
	require.Len(t, failures, 1)
	require.ErrorIs(t, failures[0], ErrServiceUnavailable)
	// A 503 page is read three times before the walk ends.
	require.Len(t, fake.requests(), pageReadAttempts)
	require.Len(t, *pauses, pageReadAttempts-1)
}
