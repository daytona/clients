// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"strconv"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestPortsIsInUse_whenReservedPortReachesToolbox(t *testing.T) {
	for _, port := range []int{2280, 2281, 22220} {
		t.Run(strconv.Itoa(port), func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /toolbox/port/" + strconv.Itoa(port) + "/in-use": func(w http.ResponseWriter, _ *http.Request) {
					w.WriteHeader(http.StatusBadRequest)
				},
			})
			sandbox := portsTestSandbox(t, fake)
			// When
			_, err := sandbox.Ports().IsInUse(t.Context(), port)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Len(t, fake.requests(), 1)
			var sdkError *Error
			require.ErrorAs(t, err, &sdkError)
			require.Empty(t, sdkError.Source) // Not local SDK validation.
		})
	}
}

func TestPortsExpose_whenSandboxIDNeedsEscaping(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"PUT /sandboxes/{id}/ports/8080": jsonHandler(t, previewEndpointFixture()),
	})
	sandbox := portsTestSandbox(t, fake)
	sandbox.id = "sbx/a +"
	// When
	_, err := sandbox.Ports().Expose(t.Context(), 8080, nil)
	// Then
	require.NoError(t, err)
	require.Equal(t, "/sandboxes/sbx%2Fa%20%2B/ports/8080", fake.requests()[0].Path)
}

func TestPortsPreviewLink_whenMintKnownUnsupported(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx/ports/8080":              jsonHandler(t, previewEndpointFixture()),
		"POST /sandboxes/sbx/ports/8080/preview-url": jsonHandler(t, map[string]string{"url": "https://route.invalid", "token": "route"}),
	})
	sandbox := portsTestSandbox(t, fake)
	sandbox.auth.mintable = true
	sandbox.auth.mintUnsupported.Store(true)
	// When
	link, err := sandbox.Ports().PreviewLink(t.Context(), 8080)
	// Then
	require.NoError(t, err)
	require.Equal(t, "route", link.Token)
	require.Len(t, fake.requests(), 2)
}

func TestPortsListEndpointsAll_whenNoContinuationEnds(t *testing.T) {
	for _, body := range []string{`{"ports":[]}`, `{"ports":[],"next_cursor":null}`, `{"ports":[],"next_cursor":""}`} {
		t.Run(body, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx/ports": jsonHandler(t, JSONValue(body)),
			})
			sandbox := portsTestSandbox(t, fake)
			// When
			count := 0
			for range sandbox.Ports().ListEndpointsAll(t.Context(), nil) {
				count++
			}
			// Then
			require.Zero(t, count)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestPortsValidation_whenInvalidSandboxID(t *testing.T) {
	for name, operation := range portsOperations() {
		if name == "listeners" || name == "in use" {
			continue
		}
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox := portsTestSandbox(t, fake)
			sandbox.id = ".."
			// When
			err := operation.call(t.Context(), sandbox.Ports())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}
