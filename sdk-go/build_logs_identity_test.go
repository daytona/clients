// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestBuildLogsDiscoveryIdentity(t *testing.T) {
	const uuid = "12345678-1234-1234-1234-123456789abc"
	const body = "3FP9SaFP7qfRTj4GM9xtju"
	tests := []struct {
		collection string
		requested  string
		actual     string
		accepted   bool
	}{
		{"images", uuid, uuid, true},
		{"images", strings.ToUpper(uuid), uuid, true},
		{"images", strings.ReplaceAll(uuid, "-", ""), uuid, true},
		{"images", "{" + uuid + "}", uuid, true},
		{"images", "urn:uuid:" + uuid, uuid, true},
		{"sandboxes", uuid, body + "-region-a", true},
		{"sandboxes", body, body + "-region-a", true},
		{"sandboxes", body + "-region-a", uuid, true},
		{"sandboxes", uuid, body + "-A_0-" + strings.Repeat("r", 60), true},
		{"sandboxes", "00000000-0000-0000-0000-000000000000", "1111111111111111-region-a", true},
		{"images", uuid, body, false},
		{"images", uuid, "00000000-0000-0000-0000-000000000000", false},
		{"sandboxes", body + "-region-a", body + "-region-b", false},
		{"sandboxes", uuid, body + "-region.a", false},
		{"sandboxes", uuid, body + "-" + strings.Repeat("r", 65), false},
		{"sandboxes", uuid, body + "-", false},
		{"sandboxes", uuid, body + "-é", false},
		{"sandboxes", uuid, "1111111111111111", false},
	}
	for _, tt := range tests {
		t.Run(tt.collection+"/"+tt.requested+"/"+tt.actual, func(t *testing.T) {
			// Given an API-discovered host with an opaque collection segment.
			target := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /prefix/build-logs/opaque/{id}": buildLogsSSE(t, buildLogsDone("empty")),
			})
			api := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /{collection}/{id}/build-logs-url": jsonHandler(t, map[string]string{
					"url": target.Server.URL + "/prefix/build-logs/opaque/" + tt.actual,
				}),
			})
			client := newTestClient(t, api.Server)
			source := buildLogSource{client, tt.collection, tt.requested}
			// When observing a resource expressed in another spelling.
			_, err := buildLogsCollect(followBuildLogs(t.Context(), source, nil))
			// Then credentials reach only an equivalent resource identity.
			require.Len(t, api.requests(), 1)
			if tt.accepted {
				require.NoError(t, err)
				require.Len(t, target.requests(), 1)
				require.Equal(t, api.requests()[0].Header.Get("Authorization"), target.requests()[0].Header.Get("Authorization"))
			} else {
				var interrupted *BuildLogInterruptedError
				require.ErrorAs(t, err, &interrupted)
				require.Equal(t, "BUILD_LOG_PROTOCOL_ERROR", interrupted.SDKError.Code)
				require.Empty(t, target.requests())
			}
		})
	}
}

func TestBuildLogsDiscoveryRejectsUnsafeRoutes(t *testing.T) {
	targets := []string{
		"//logs.invalid/build-logs/images/image",
		"ftp://logs.invalid/build-logs/images/image",
		"https://user:pass@logs.invalid/build-logs/images/image",
		"https://logs.invalid/build-logs/images/image?q=x",
		"https://logs.invalid/build-logs/images/image#x",
		"https://logs.invalid/\\evil",
		"https://logs.invalid/\nlogs",
		"https://logs.invalid/arbitrary",
		"https://logs.invalid/build-logs/images/other",
		"https://logs.invalid/build-logs/images/image/",
		"https://logs.invalid/build-logs//image",
		"https://logs.invalid/build-logs/images/%69mage",
		"https://logs.invalid/build-logs/../image",
		"https://logs.invalid/build-logs/./image",
		"https://logs.invalid/build-logs/%2e%2E/image",
		"https://logs.invalid:99999/build-logs/images/image",
	}
	for _, target := range targets {
		t.Run(target, func(t *testing.T) {
			// Given an untrusted discovery URL.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /images/image/build-logs-url": jsonHandler(t, map[string]string{"url": target}),
			})
			client := newTestClient(t, fake.Server)
			// When following.
			_, err := buildLogsCollect((&Images{client: client}).FollowBuildLogs(t.Context(), "image", nil))
			// Then discovery fails before any connection to its destination.
			var interrupted *BuildLogInterruptedError
			require.ErrorAs(t, err, &interrupted)
			require.Equal(t, "BUILD_LOG_PROTOCOL_ERROR", interrupted.SDKError.Code)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestBuildLogsDiscoveryKeepsOpaqueCollectionEncoding(t *testing.T) {
	// Given a collection with percent encoding, which TypeScript leaves opaque.
	target := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /": buildLogsSSE(t, buildLogsDone("empty")),
	})
	path := "/prefix/build-logs/images%2Fnested/image"
	api := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /images/image/build-logs-url": jsonHandler(t, map[string]string{"url": target.Server.URL + path}),
	})
	client := newTestClient(t, api.Server)
	// When following the discovered route.
	_, err := buildLogsCollect((&Images{client: client}).FollowBuildLogs(t.Context(), "image", nil))
	// Then only the resource ID is normalized; the collection path stays opaque.
	require.NoError(t, err)
	require.Len(t, target.requests(), 1)
	require.Equal(t, path, target.requests()[0].Path)
}
