// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/base64"
	"fmt"
	"net/http"
	"strconv"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestPortsPreviewLink_whenPublic(t *testing.T) {
	// Given
	endpoint := previewEndpointFixture()
	endpoint["public"] = true
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx/ports/8080": jsonHandler(t, endpoint),
	})
	sandbox := portsTestSandbox(t, fake)
	// When
	link, err := sandbox.Ports().PreviewLink(t.Context(), 8080)
	// Then
	require.NoError(t, err)
	require.Equal(t, "https://preview.invalid", link.URL)
	require.Empty(t, link.Token)
	require.True(t, link.ExpiresAt.IsZero())
	require.Len(t, fake.requests(), 1)
}

func TestPortsPreviewLink_whenMissingRegistersPrivately(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx/ports/8080":              func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(404) },
		"PUT /sandboxes/sbx/ports/8080":              jsonHandler(t, previewEndpointFixture()),
		"POST /sandboxes/sbx/ports/8080/preview-url": jsonHandler(t, map[string]string{"url": "", "token": "opaque"}),
	})
	sandbox := portsTestSandbox(t, fake)
	// When
	link, err := sandbox.Ports().PreviewLink(t.Context(), 8080)
	// Then
	require.NoError(t, err)
	require.Equal(t, "https://preview.invalid", link.URL)
	require.Equal(t, "opaque", link.Token)
	require.Len(t, fake.requests(), 3)
	require.JSONEq(t, `{"public":false}`, string(fake.requests()[1].Body))
	require.Empty(t, fake.requests()[2].Body)
}

func TestPortsPreviewLink_whenScopedMintSucceeds(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx/ports/8080":              jsonHandler(t, previewEndpointFixture()),
		"POST /sandboxes/sbx/ports/8080/preview-url": jsonHandler(t, map[string]string{"url": "https://route.invalid", "token": "opaque"}),
		"POST /sandboxes/sbx/access-token": jsonHandler(t, map[string]any{
			"access_token": "scoped", "token_type": "Bearer", "expires_at": "2027-01-01T00:00:00Z",
			"operations": []string{}, "ports": []int{8080},
		}),
	})
	sandbox := portsTestSandbox(t, fake)
	sandbox.auth.mintable = true
	// When
	link, err := sandbox.Ports().PreviewLink(t.Context(), 8080)
	// Then
	require.NoError(t, err)
	require.Equal(t, "scoped", link.Token)
	require.Equal(t, "https://route.invalid", link.URL)
	require.Equal(t, time.Date(2027, 1, 1, 0, 0, 0, 0, time.UTC), link.ExpiresAt)
	require.JSONEq(t, `{"operations":[],"ports":[8080]}`, string(fake.requests()[2].Body))
	require.Equal(t, "Bearer test-key", fake.requests()[2].Header.Get("Authorization"))
	require.Equal(t, "caller-token", sandbox.auth.cache.snapshot().token)
}

func TestPortsPreviewLink_whenMintFailsKeepsRouteToken(t *testing.T) {
	for _, status := range []int{404, 405, 501, 500, 403} {
		t.Run(strconv.Itoa(status), func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx/ports/8080":              jsonHandler(t, previewEndpointFixture()),
				"POST /sandboxes/sbx/ports/8080/preview-url": jsonHandler(t, map[string]string{"url": "https://route.invalid", "token": "opaque"}),
				"POST /sandboxes/sbx/access-token":           func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(status) },
			})
			sandbox := portsTestSandbox(t, fake)
			sandbox.auth.mintable = true
			// When
			link, err := sandbox.Ports().PreviewLink(t.Context(), 8080)
			// Then
			require.NoError(t, err)
			require.Equal(t, "opaque", link.Token)
			require.Equal(t, status == 405 || status == 501, sandbox.auth.mintUnsupported.Load())
		})
	}
}

func TestPortsPreviewLink_whenCallerSuppliedTokenKeepsAdvisoryExpiry(t *testing.T) {
	for _, test := range []struct {
		payload string
		expiry  time.Time
	}{
		{`{"exp":1900000000.1239}`, time.UnixMilli(1900000000123).UTC()},
		{`{"exp":true}`, time.Time{}},
		{`{"exp":-1}`, time.Time{}},
		{`{"exp":1e300}`, time.Time{}},
		{`{"exp":"1900000000"}`, time.Time{}},
		{`[]`, time.Time{}},
		{`{"exp":null}`, time.Time{}},
		{`{"exp":0}`, time.Time{}},
	} {
		t.Run(test.payload, func(t *testing.T) {
			// Given
			token := "header." + base64.RawURLEncoding.EncodeToString([]byte(test.payload)) + ".signature"
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx/ports/8080":              jsonHandler(t, previewEndpointFixture()),
				"POST /sandboxes/sbx/ports/8080/preview-url": jsonHandler(t, map[string]string{"url": "https://route.invalid", "token": token}),
			})
			sandbox := portsTestSandbox(t, fake)
			// When
			link, err := sandbox.Ports().PreviewLink(t.Context(), 8080)
			// Then
			require.NoError(t, err)
			require.Equal(t, token, link.Token)
			require.Equal(t, test.expiry, link.ExpiresAt)
			require.Len(t, fake.requests(), 2)
		})
	}
}

func TestPortsSignedPreviewLink_whenLifetimeSelected(t *testing.T) {
	for _, test := range []struct {
		name string
		opts *SignedPreviewLinkOptions
		body string
	}{
		{"nil", nil, `{}`},
		{"zero", &SignedPreviewLinkOptions{}, `{}`},
		{"explicit", &SignedPreviewLinkOptions{ExpiresIn: 300 * time.Second}, `{"expires_in_seconds":300}`},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes/sbx/ports/8080/signed-preview-urls": jsonHandler(t, map[string]string{
					"url": "https://signed.invalid", "token": "signed", "expires_at": "2027-01-01T00:00:00Z",
				}),
			})
			sandbox := portsTestSandbox(t, fake)
			// When
			link, err := sandbox.Ports().SignedPreviewLink(t.Context(), 8080, test.opts)
			// Then
			require.NoError(t, err)
			require.Equal(t, "https://signed.invalid", link.URL)
			require.Equal(t, "signed", link.Token)
			require.Equal(t, time.Date(2027, 1, 1, 0, 0, 0, 0, time.UTC), link.ExpiresAt)
			require.JSONEq(t, test.body, string(fake.requests()[0].Body))
		})
	}
}

func TestPortsSignedPreviewLink_whenLifetimeInvalid(t *testing.T) {
	for _, lifetime := range []time.Duration{-time.Second, NoTimeout, time.Nanosecond, time.Second + time.Nanosecond} {
		t.Run(lifetime.String(), func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox := portsTestSandbox(t, fake)
			// When
			_, err := sandbox.Ports().SignedPreviewLink(t.Context(), 8080, &SignedPreviewLinkOptions{ExpiresIn: lifetime})
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestPreviewModels_whenFormattedHideCredentials(t *testing.T) {
	for _, value := range []any{
		PreviewLink{URL: "sensitive-url", Token: "sensitive-token"},
		SignedPreviewLink{URL: "sensitive-url", Token: "sensitive-token"},
	} {
		// Given
		t.Run(fmt.Sprintf("%T", value), func(t *testing.T) {
			// When
			shown := fmt.Sprintf("%v %+v %#v", value, value, value)
			// Then
			require.NotContains(t, shown, "sensitive")
		})
	}
}
