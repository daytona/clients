// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"strconv"
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

func TestCreateSnapshotRejectsSeparatorOnlyName(t *testing.T) {
	for _, name := range []string{"\x1c", "\x1d", "\x1e", "\x1f", " \t\x1c\x1d\x1e\x1f\u0085"} {
		t.Run(strconv.Quote(name), func(t *testing.T) {
			// Given a name containing only whitespace recognized by the sibling SDKs.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes/sbx/snapshots": jsonHandler(t, map[string]string{"id": "snap"}),
				"GET /snapshots/snap":           jsonHandler(t, snapshotFixture("snap", "creating")),
			})
			client := newTestClient(t, fake.Server)
			sandbox, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "sbx"}})
			require.NoError(t, err)

			// When creating a snapshot.
			_, err = sandbox.CreateSnapshot(t.Context(), name)

			// Then blank names fail locally rather than creating a snapshot.
			require.ErrorIs(t, err, ErrBadRequest)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, SourceSDK, detail.Source)
			require.Empty(t, fake.requests())
		})
	}
}

func TestCreateSnapshotPreservesNameWithSeparators(t *testing.T) {
	// Given a non-blank name surrounded by whitespace.
	name := "\x1csaved\x1f"
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/sbx/snapshots": jsonHandler(t, map[string]string{"id": "snap"}),
		"GET /snapshots/snap":           jsonHandler(t, snapshotFixture("snap", "creating")),
	})
	client := newTestClient(t, fake.Server)
	sandbox, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "sbx"}})
	require.NoError(t, err)

	// When creating a snapshot.
	snapshot, err := sandbox.CreateSnapshot(t.Context(), name)

	// Then blank checking does not trim the submitted name.
	require.NoError(t, err)
	require.Equal(t, "snap", snapshot.ID)
	requests := fake.requests()
	require.Len(t, requests, 2)
	require.JSONEq(t, `{"name":"\u001csaved\u001f"}`, string(requests[0].Body))
}
