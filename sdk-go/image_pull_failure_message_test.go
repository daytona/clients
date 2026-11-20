// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestImagePull_whenFailureHasNoReason_namesThePull(t *testing.T) {
	// Given an import that fails without a recorded error
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /images":    jsonHandler(t, map[string]string{"id": "img"}),
		"GET /images/img": jsonHandler(t, imageFixture("img", "failed")),
	})
	client := newTestClient(t, fake.Server)
	// When
	_, err := client.Images().Pull(t.Context(), "alpine:3", nil)
	// Then the message names the pull, as the TypeScript SDK's does
	var failure *ImageBuildFailedError
	require.ErrorAs(t, err, &failure)
	require.Contains(t, err.Error(), "image pull img failed")
}
