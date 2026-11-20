// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/http"
	"net/url"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestSignedFileURL_whenMinted_usesHandleBaseAndServerExpiry(t *testing.T) {
	for _, method := range []string{http.MethodGet, http.MethodPost} {
		t.Run(method, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /sandboxes/sbx/files/sign": jsonHandler(t,
				map[string]string{"signature": "signed", "expires_at": "2030-01-01T00:00:00Z", "url": "ignored"})})
			client := newTestClient(t, fake.Server)
			sb, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "sbx"}, ToolboxURL: fake.Server.URL + "/proxy"})
			require.NoError(t, err)
			before := time.Now().UTC().Truncate(time.Second)
			sign := sb.FS().PresignedDownloadURL
			route := "/proxy/sbx/files/download"
			if method == http.MethodPost {
				sign, route = sb.FS().PresignedUploadURL, "/proxy/sbx/files/upload"
			}
			// When
			link, err := sign(t.Context(), "/a b/é?#", &PresignedFileURLOptions{ExpiresIn: SignedFileMaxTTL})
			// Then
			require.NoError(t, err)
			parsed, err := url.Parse(link)
			require.NoError(t, err)
			require.Equal(t, route, parsed.Path)
			require.Equal(t, "/a b/é?#", parsed.Query().Get("path"))
			require.Equal(t, "1893456000", parsed.Query().Get("expires"))
			require.Equal(t, "signed", parsed.Query().Get("signature"))
			var request struct {
				Method, Path string
				ExpiresAt    time.Time `json:"expires_at"`
			}
			require.NoError(t, json.Unmarshal(fake.requests()[0].Body, &request))
			require.Equal(t, method, request.Method)
			require.Equal(t, "/a b/é?#", request.Path)
			require.False(t, request.ExpiresAt.Before(before.Add(SignedFileMaxTTL)))
			require.False(t, request.ExpiresAt.After(time.Now().UTC().Truncate(time.Second).Add(SignedFileMaxTTL)))
		})
	}
}

func TestSignedFileURL_whenInvalidTTL_neverRequests(t *testing.T) {
	for _, ttl := range []time.Duration{-time.Second, NoTimeout, time.Millisecond, time.Second + time.Nanosecond, SignedFileMaxTTL + time.Second} {
		// Given
		fake := newFakeServer(t, map[string]http.HandlerFunc{})
		fs := newFileTestService(t, fake.Server)
		// When
		_, err := fs.PresignedUploadURL(t.Context(), "/a", &PresignedFileURLOptions{ExpiresIn: ttl})
		// Then
		require.ErrorIs(t, err, ErrBadRequest)
		require.Empty(t, fake.requests())
	}
}

func TestSignedFileURL_whenUnscopedOverride_refusesSigning(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{})
	fs := newFileTestService(t, fake.Server)
	// When
	_, err := fs.PresignedDownloadURL(t.Context(), "/a", nil)
	// Then
	require.ErrorIs(t, err, ErrUnsupportedOperation)
	require.Empty(t, fake.requests())
}

func TestSignedFileURL_whenPlaceholderInsidePath_preservesOverride(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /sandboxes/sbx/files/sign": jsonHandler(t,
		map[string]string{"signature": "signed", "expires_at": "2030-01-01T00:00:00Z"})})
	client := newTestClient(t, fake.Server)
	sb, err := client.newSandbox(sandboxInit{
		Info:    SandboxInfo{ID: "sbx"},
		Options: GetOptions{ToolboxURL: fake.Server.URL + "/proxy/{sandboxId}/custom"},
	})
	require.NoError(t, err)
	// When
	link, err := sb.FS().PresignedDownloadURL(t.Context(), "/a", nil)
	// Then
	require.NoError(t, err)
	parsed, err := url.Parse(link)
	require.NoError(t, err)
	require.Equal(t, "/proxy/sbx/custom/files/download", parsed.Path)
}

func TestSignedFileURL_whenMalformedResponse(t *testing.T) {
	for _, payload := range []map[string]any{
		{"signature": "", "expires_at": "2030-01-01T00:00:00Z"},
		{"signature": "x", "expires_at": "tomorrow"},
		{"signature": "x"},
	} {
		// Given
		fake := newFakeServer(t, map[string]http.HandlerFunc{
			"POST /sandboxes/sbx/files/sign": jsonHandler(t, payload),
		})
		client := newTestClient(t, fake.Server)
		sb, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "sbx"}, ToolboxURL: fake.Server.URL})
		require.NoError(t, err)
		// When
		_, err = sb.FS().PresignedDownloadURL(t.Context(), "/a", nil)
		// Then
		require.ErrorIs(t, err, ErrBadRequest)
	}
}
