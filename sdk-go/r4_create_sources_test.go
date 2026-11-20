// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestCreateSources_whenInvalidUTF8_rejectBeforeRequest(t *testing.T) {
	calls := []struct {
		name string
		call func(context.Context, *Client) (*Sandbox, error)
	}{
		{"image", func(ctx context.Context, c *Client) (*Sandbox, error) {
			return c.Create(ctx, "image-\xff", &CreateOptions{Wait: NoWait})
		}},
		{"snapshot", func(ctx context.Context, c *Client) (*Sandbox, error) {
			return c.CreateFromSnapshot(ctx, "snapshot-\xff", &CreateFromSnapshotOptions{Wait: NoWait})
		}},
		{"macos", func(ctx context.Context, c *Client) (*Sandbox, error) {
			return c.CreateMacOS(ctx, "image-\xff", &CreateMacOSOptions{Wait: NoWait})
		}},
	}
	for _, route := range calls {
		t.Run(route.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes":               jsonHandler(t, map[string]string{"id": "accepted"}),
				"POST /sandboxes/from-snapshot": jsonHandler(t, map[string]string{"id": "accepted"}),
				"POST /sandboxes/macos":         jsonHandler(t, map[string]string{"id": "accepted"}),
			})
			client := newTestClient(t, fake.Server)
			// When
			sandbox, err := route.call(t.Context(), client)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Nil(t, sandbox)
			require.Empty(t, fake.requests())
		})
	}
}
