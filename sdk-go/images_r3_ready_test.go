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

func TestImageReadinessBoundsHold_whenBudgetIsUnbounded(t *testing.T) {
	for _, operation := range []struct {
		name string
		run  func(context.Context, *Images) (*Image, error)
	}{
		{"pull", func(ctx context.Context, images *Images) (*Image, error) {
			return images.Pull(ctx, "alpine", nil)
		}},
		{"build", func(ctx context.Context, images *Images) (*Image, error) {
			return images.BuildAndWait(ctx, NewImageBuilder("alpine"), nil)
		}},
	} {
		t.Run(operation.name, func(t *testing.T) {
			// Given a first hold that stalls beyond its server budget and read slack.
			holds := 0
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /images": jsonHandler(t, map[string]string{"id": "img"}),
				"GET /images/img": func(w http.ResponseWriter, r *http.Request) {
					status := "pending"
					if r.URL.Query().Has("wait") {
						holds++
						if holds == 1 && imageDelay(r.Context(), 70*time.Second) != nil {
							return
						}
						status = "ready"
					}
					writeJSON(t, w, imageFixture("img", status))
				},
			})
			synctest.Test(t, func(t *testing.T) {
				client := imageClockClient(t, fake, time.Second)
				start := time.Now()
				// When readiness has no caller or SDK deadline.
				_, err := operation.run(t.Context(), client.Images())
				// Then the stalled read ends after the 60-second hold plus slack and
				// is retried after the first backoff.
				require.NoError(t, err)
				require.Equal(t, 65*time.Second+waitMinInterval, time.Since(start))
				requests := fake.requests()
				require.Len(t, requests, 4)
				require.Equal(t, "60000", requests[2].Query.Get("timeout_ms"))
				require.Equal(t, "60000", requests[3].Query.Get("timeout_ms"))
			})
		})
	}
}
