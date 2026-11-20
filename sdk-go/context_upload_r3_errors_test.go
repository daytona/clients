// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestContextUploadExplainsExpiry_whenStorageRefusesPart(t *testing.T) {
	// Given a storage URL whose authorization has expired.
	server := contextStorageServer(t, func(w http.ResponseWriter, r *http.Request) {
		contextDrainRequest(t, r)
		w.WriteHeader(http.StatusForbidden)
	})
	client := newTestClient(t, server)
	uploader := contextTestUploader(t, client, []byte("abc"))
	// When storage refuses the upload.
	err := uploader.putPart(t.Context(), contextUploadPart{number: 1, bytes: 3, url: server.URL + "/part"})
	// Then the diagnostic gives the actionable recovery without exposing the URL.
	require.ErrorIs(t, err, ErrBadRequest)
	require.ErrorContains(t, err, "its upload URL may have expired, start the build again")
	require.NotContains(t, err.Error(), server.URL)
}

func TestContextUploadUsesCanonicalError_whenStorageRedirects(t *testing.T) {
	// Given storage redirecting a signed part upload.
	server := contextStorageServer(t, func(w http.ResponseWriter, r *http.Request) {
		contextDrainRequest(t, r)
		w.Header().Set("Location", "/must-not-follow")
		w.WriteHeader(http.StatusTemporaryRedirect)
	})
	client := newTestClient(t, server)
	uploader := contextTestUploader(t, client, []byte("abc"))
	// When the upload encounters a redirect.
	err := uploader.putPart(t.Context(), contextUploadPart{number: 1, bytes: 3, url: server.URL + "/part"})
	// Then it uses the same SDK redirect contract as API requests.
	var detail *Error
	require.ErrorAs(t, err, &detail)
	require.Equal(t, http.StatusTemporaryRedirect, detail.StatusCode)
	require.Equal(t, SourceSDK, detail.Source)
	require.Equal(t, "unexpected redirect (the SDK does not follow redirects)", detail.Message)
}
