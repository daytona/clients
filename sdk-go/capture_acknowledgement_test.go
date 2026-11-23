// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestArtifactCreate_whenAcknowledgementIDIsLossy(t *testing.T) {
	for _, operation := range []struct {
		name string
		path string
		call func(context.Context, *Client, *Sandbox) error
	}{
		{"capture", "/sandboxes/sbx/images", func(ctx context.Context, _ *Client, sandbox *Sandbox) error {
			_, err := sandbox.CaptureImage(ctx, nil)
			return err
		}},
		{"snapshot", "/sandboxes/sbx/snapshots", func(ctx context.Context, _ *Client, sandbox *Sandbox) error {
			_, err := sandbox.CreateSnapshot(ctx, "saved")
			return err
		}},
		{"pull", "/images", func(ctx context.Context, client *Client, _ *Sandbox) error {
			_, err := client.Images().Pull(ctx, "alpine", nil)
			return err
		}},
		{"build", "/images", func(ctx context.Context, client *Client, _ *Sandbox) error {
			_, err := client.Images().Build(ctx, ImageFromDockerfile("FROM alpine"), nil)
			return err
		}},
	} {
		for _, id := range []string{`"\ud800"`, `"\udfff"`, `"replacement-\ufffd"`} {
			t.Run(operation.name+"/"+id, func(t *testing.T) {
				// Given an acknowledgement whose identity cannot safely be reused.
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"POST " + operation.path: jsonHandler(t, json.RawMessage(`{"id":`+id+`}`)),
				})
				client := newTestClient(t, fake.Server)
				sandbox, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "sbx"}})
				require.NoError(t, err)
				// When accepting the asynchronous operation.
				err = operation.call(t.Context(), client, sandbox)
				// Then the response is rejected before a lossy follow-up request.
				require.ErrorIs(t, err, ErrBadRequest)
				require.Len(t, fake.requests(), 1)
			})
		}
	}
}

func TestContextUpload_whenAcknowledgementIDIsLossy(t *testing.T) {
	for _, state := range []string{"pending", "ready"} {
		t.Run(state, func(t *testing.T) {
			// Given an upload acknowledgement with an escaped lone surrogate.
			archive := contextTestArchive(t, []byte("x"))
			storage := contextStorageServer(t, func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(http.StatusNoContent)
			})
			declaration := contextTestDeclaration([]map[string]any{contextTestPart(1, 1, storage.URL)})
			declaration["id"] = json.RawMessage(`"\ud800"`)
			declaration["state"] = state
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /build-contexts": jsonHandler(t, declaration),
			})
			client := newTestClient(t, fake.Server)
			// When uploading a context.
			_, err := client.Images().uploadContext(t.Context(), archive, "")
			// Then neither reuse nor completion can consume the corrupted identity.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Len(t, fake.requests(), 1)
		})
	}
}
