// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"iter"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestVolumeSecretList_whenContinuationExists(t *testing.T) {
	for _, resource := range []string{"volumes", "secrets"} {
		t.Run(resource, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /" + resource: jsonHandler(t, map[string]any{resource: []any{}, "next_cursor": "next"}),
			})
			client := newTestClient(t, fake.Server)
			var cursor string
			var err error
			// When
			switch resource {
			case "volumes":
				var page *Page[Volume]
				page, err = client.Volumes().List(t.Context(), &ListVolumesOptions{Cursor: "start", Limit: 2})
				if err == nil {
					cursor = page.NextCursor
				}
			case "secrets":
				var page *Page[Secret]
				page, err = client.Secrets().List(t.Context(), &ListSecretsOptions{Cursor: "start", Limit: 2})
				if err == nil {
					cursor = page.NextCursor
				}
			}
			// Then
			require.NoError(t, err)
			require.Equal(t, "next", cursor)
			requests := fake.requests()
			require.Len(t, requests, 1)
			require.Equal(t, "start", requests[0].Query.Get("cursor"))
			require.Equal(t, "2", requests[0].Query.Get("limit"))
		})
	}
}

func TestVolumeSecretListAll_whenConsumerStopsOrCursorCycles(t *testing.T) {
	for _, resource := range []string{"volumes", "secrets"} {
		for _, stop := range []bool{false, true} {
			t.Run(resource+map[bool]string{true: "_break", false: "_cycle"}[stop], func(t *testing.T) {
				// Given
				var fixture any = volumeFixture()
				if resource == "secrets" {
					fixture = secretFixture()
				}
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"GET /" + resource: func(w http.ResponseWriter, r *http.Request) {
						next := "second"
						if r.URL.Query().Get("cursor") == "second" {
							next = "first"
						}
						writeJSON(t, w, map[string]any{resource: []any{fixture}, "next_cursor": next})
					},
				})
				client := newTestClient(t, fake.Server)
				var sequence iter.Seq2[string, error]
				switch resource {
				case "volumes":
					opts := &ListVolumesOptions{Cursor: "first", Limit: 1}
					items := client.Volumes().ListAll(t.Context(), opts)
					opts.Cursor, opts.Limit = "mutated", 200
					sequence = func(yield func(string, error) bool) {
						for item, err := range items {
							if !yield(item.ID, err) {
								return
							}
						}
					}
				case "secrets":
					opts := &ListSecretsOptions{Cursor: "first", Limit: 1}
					items := client.Secrets().ListAll(t.Context(), opts)
					opts.Cursor, opts.Limit = "mutated", 200
					sequence = func(yield func(string, error) bool) {
						for item, err := range items {
							if !yield(item.Name, err) {
								return
							}
						}
					}
				}
				// When
				var got []string
				for item, err := range sequence {
					require.NoError(t, err)
					got = append(got, item)
					if stop {
						break
					}
				}
				// Then
				expected := 2
				if stop {
					expected = 1
				}
				require.Len(t, got, expected)
				requests := fake.requests()
				require.Len(t, requests, expected)
				require.Equal(t, "first", requests[0].Query.Get("cursor"))
				require.Equal(t, "1", requests[0].Query.Get("limit"))
			})
		}
	}
}

func TestVolumeSecretCalls_whenCanceledOrClosed(t *testing.T) {
	operations := []struct {
		name string
		call func(context.Context, *Client) error
	}{
		{"volume list", func(ctx context.Context, c *Client) error { _, err := c.Volumes().List(ctx, nil); return err }},
		{"volume get", func(ctx context.Context, c *Client) error { _, err := c.Volumes().Get(ctx, "v", nil); return err }},
		{"volume create", func(ctx context.Context, c *Client) error { _, err := c.Volumes().Create(ctx, "v", nil); return err }},
		{"volume delete", func(ctx context.Context, c *Client) error { return c.Volumes().Delete(ctx, "v", nil) }},
		{"secret list", func(ctx context.Context, c *Client) error { _, err := c.Secrets().List(ctx, nil); return err }},
		{"secret get", func(ctx context.Context, c *Client) error { _, err := c.Secrets().Get(ctx, "S"); return err }},
		{"secret set", func(ctx context.Context, c *Client) error {
			_, err := c.Secrets().Set(ctx, "S", "v", &SetSecretOptions{AllowedDomains: []string{"*"}})
			return err
		}},
		{"secret delete", func(ctx context.Context, c *Client) error { return c.Secrets().Delete(ctx, "S") }},
	}
	for _, tt := range operations {
		t.Run(tt.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			ctx, cancel := context.WithCancel(t.Context())
			cancel()
			// When
			err := tt.call(ctx, client)
			// Then
			require.ErrorIs(t, err, context.Canceled)
			require.Empty(t, fake.requests())
		})
		t.Run(tt.name+" closed", func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			require.NoError(t, client.Close())
			// When
			err := tt.call(t.Context(), client)
			// Then
			require.ErrorIs(t, err, ErrClientClosed)
			require.Empty(t, fake.requests())
		})
	}
}
