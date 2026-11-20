// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestCaptureImageRejectsNamedIdleExpiry(t *testing.T) {
	for _, expiry := range []time.Duration{time.Second, WaitForever} {
		t.Run(expiry.String(), func(t *testing.T) {
			// Given a named capture with an anonymous-only retention option.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes/sbx/images": jsonHandler(t, map[string]string{"id": "img"}),
				"GET /images/img":            jsonHandler(t, imageFixture("img", "pending")),
			})
			client := newTestClient(t, fake.Server)
			sandbox, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "sbx"}})
			require.NoError(t, err)
			// When submitting both options.
			_, err = sandbox.CaptureImage(t.Context(), &CaptureImageOptions{Name: "saved", IdleExpiry: expiry})
			// Then validation fails locally before the capture request.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestCreateSnapshotRejectsInvalidUTF8Name(t *testing.T) {
	// Given a name encoding/json would silently replace.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/sbx/snapshots": jsonHandler(t, map[string]string{"id": "snap"}),
		"GET /snapshots/snap":           jsonHandler(t, snapshotFixture("snap", "creating")),
	})
	client := newTestClient(t, fake.Server)
	sandbox, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "sbx"}})
	require.NoError(t, err)
	// When creating a snapshot.
	_, err = sandbox.CreateSnapshot(t.Context(), "saved\xff")
	// Then the name is rejected before it can be changed on the wire.
	require.ErrorIs(t, err, ErrBadRequest)
	require.Empty(t, fake.requests())
}
