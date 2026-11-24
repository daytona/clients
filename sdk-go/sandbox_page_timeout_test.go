// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestSandboxPageKeepsItem_whenDetailTimesOut(t *testing.T) {
	for name, fetch := range sandboxPageCalls() {
		t.Run(name, func(t *testing.T) {
			// Given: one item needs a detail read, one is already complete.
			item := sandboxFixture("slow")
			delete(item, "toolbox_proxy_url")
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/search": jsonHandler(t, map[string]any{
					"sandboxes": []any{item, sandboxFixture("ready")}, "total": 2, "total_relation": "eq", "next_cursor": "next",
				}),
				"GET /sandboxes/slow": func(_ http.ResponseWriter, r *http.Request) { <-r.Context().Done() },
			})
			synctest.Test(t, func(t *testing.T) {
				client := imageClockClient(t, fake, time.Second)
				// When
				page, err := fetch(t.Context(), client)
				// Then
				require.NoError(t, err)
				require.Len(t, page.Items, 2)
				require.Equal(t, "slow", page.Items[0].ID())
				require.Equal(t, SandboxStateStarted, page.Items[0].State())
				require.Equal(t, "ready", page.Items[1].ID())
				require.Equal(t, "next", page.NextCursor)
			})
		})
	}
}

func TestSandboxPageStops_whenCallerOrClientEndsDetail(t *testing.T) {
	for name, fetch := range sandboxPageCalls() {
		for _, end := range []string{"cancel", "deadline", "close"} {
			t.Run(name+"/"+end, func(t *testing.T) {
				// Given
				item := sandboxFixture("slow")
				delete(item, "toolbox_proxy_url")
				arrived := make(chan struct{}, 1)
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"GET /sandboxes/search": jsonHandler(t, map[string]any{
						"sandboxes": []any{item}, "total": 1, "total_relation": "eq",
					}),
					"GET /sandboxes/slow": func(_ http.ResponseWriter, r *http.Request) {
						arrived <- struct{}{}
						<-r.Context().Done()
					},
				})
				synctest.Test(t, func(t *testing.T) {
					client := imageClockClient(t, fake, NoTimeout)
					ctx, cancel := contextEnd(t, end == "deadline")
					defer cancel()
					go func() {
						<-arrived
						switch end {
						case "cancel":
							cancel()
						case "close":
							if err := client.Close(); err != nil {
								t.Error(err)
							}
						}
					}()
					// When
					page, err := fetch(ctx, client)
					// Then
					require.Nil(t, page)
					if end == "close" {
						require.ErrorIs(t, err, ErrClientClosed)
					} else {
						require.ErrorIs(t, err, contextEndError(end == "deadline"))
					}
				})
			})
		}
	}
}

func sandboxPageCalls() map[string]func(context.Context, *Client) (*Page[*Sandbox], error) {
	return map[string]func(context.Context, *Client) (*Page[*Sandbox], error){
		"list": func(ctx context.Context, c *Client) (*Page[*Sandbox], error) { return c.List(ctx, nil) },
		"search": func(ctx context.Context, c *Client) (*Page[*Sandbox], error) {
			page, err := c.Search(ctx, nil)
			if err != nil {
				return nil, err
			}
			return &page.Page, nil
		},
	}
}
