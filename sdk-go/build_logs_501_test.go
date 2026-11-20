// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestBuildLogsRetries501LikeSiblingSDKs(t *testing.T) {
	for _, phase := range []string{"discovery", "stream"} {
		t.Run(phase, func(t *testing.T) {
			// Given a 501 followed by recovery at either build-log HTTP boundary.
			var discoveries, streams atomic.Int32
			api := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /images/image/build-logs-url": func(w http.ResponseWriter, r *http.Request) {
					if discoveries.Add(1) == 1 && phase == "discovery" {
						w.WriteHeader(http.StatusNotImplemented)
						return
					}
					writeJSON(t, w, map[string]string{"url": "http://" + r.Host + "/build-logs/images/image"})
				},
				"GET /build-logs/images/image": func(w http.ResponseWriter, r *http.Request) {
					if streams.Add(1) == 1 && phase == "stream" {
						w.WriteHeader(http.StatusNotImplemented)
						return
					}
					buildLogsSSE(t, buildLogsDone("empty"))(w, r)
				},
			})
			client := newTestClient(t, api.Server)
			var pauses int
			follower := buildLogFollower{
				api: client.api(), path: "/images/image/build-logs-url", now: time.Now,
				jitter: func() float64 { return 1 },
				pause: func(context.Context, time.Duration) error {
					pauses++
					return nil
				},
			}
			var delivered []BuildLogEvent

			// When following with deterministic reconnect scheduling.
			err := follower.follow(t.Context(), func(event BuildLogEvent, _ error) bool {
				delivered = append(delivered, event)
				return true
			})

			// Then 501 is retried like every 5xx, matching TypeScript and Python.
			require.NoError(t, err)
			require.Equal(t, 1, pauses)
			require.Len(t, delivered, 1)
			require.Len(t, api.requests(), 3)
		})
	}
}
