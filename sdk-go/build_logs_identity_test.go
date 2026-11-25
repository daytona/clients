// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"strings"
	"sync/atomic"
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
				// A refused image is looked up first (the value may be a name):
				// the lookup confirms the requested ID.
				"GET /images/{id}": jsonHandler(t, map[string]string{"id": tt.requested}),
			})
			client := newTestClient(t, api.Server)
			source := buildLogSource{client, tt.collection, tt.requested}
			// When observing a resource expressed in another spelling.
			_, err := buildLogsCollect(followBuildLogs(t.Context(), source, nil))
			// Then credentials reach only an equivalent resource identity.
			lookups := 0
			if !tt.accepted && tt.collection == "images" {
				lookups = 1
			}
			require.Len(t, api.requests(), 1+lookups)
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
				// A URL naming another image looks the requested value up first.
				"GET /images/image": jsonHandler(t, map[string]string{"id": "image"}),
			})
			client := newTestClient(t, fake.Server)
			// When following.
			_, err := buildLogsCollect((&Images{client: client}).FollowBuildLogs(t.Context(), "image", nil))
			// Then discovery fails before any connection to its destination.
			var interrupted *BuildLogInterruptedError
			require.ErrorAs(t, err, &interrupted)
			require.Equal(t, "BUILD_LOG_PROTOCOL_ERROR", interrupted.SDKError.Code)
			requests := fake.requests()
			require.Equal(t, "/images/image/build-logs-url", requests[0].Path)
			for _, request := range requests[1:] {
				require.Equal(t, "/images/image", request.Path)
			}
			require.LessOrEqual(t, len(requests), 2)
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

func TestBuildLogsFollowImageByName(t *testing.T) {
	const uuid = "12345678-1234-1234-1234-123456789abc"
	for _, tt := range []struct {
		name   string
		lookup http.HandlerFunc
		check  func(t *testing.T, err error, api, target *fakeServer)
	}{
		{"resolved to the destination's ID", nil, func(t *testing.T, err error, api, target *fakeServer) {
			require.NoError(t, err)
			require.Len(t, target.requests(), 1)
			require.Equal(t, "/images/my-image", api.requests()[1].Path)
		}},
		{"resolved to another ID", func(w http.ResponseWriter, _ *http.Request) {
			writeJSON(t, w, map[string]string{"id": "00000000-0000-0000-0000-000000000000"})
		}, func(t *testing.T, err error, api, target *fakeServer) {
			var interrupted *BuildLogInterruptedError
			require.ErrorAs(t, err, &interrupted)
			require.Equal(t, "BUILD_LOG_PROTOCOL_ERROR", interrupted.SDKError.Code)
			require.Empty(t, target.requests())
			require.Len(t, api.requests(), 2)
		}},
		{"answered without an ID", func(w http.ResponseWriter, _ *http.Request) {
			writeJSON(t, w, map[string]string{"name": "my-image"})
		}, func(t *testing.T, err error, _, target *fakeServer) {
			var interrupted *BuildLogInterruptedError
			require.ErrorAs(t, err, &interrupted)
			require.Equal(t, "BUILD_LOG_PROTOCOL_ERROR", interrupted.SDKError.Code)
			require.Contains(t, interrupted.Error(), "invalid image response: id")
			require.Empty(t, target.requests())
		}},
		{"unavailable once", unavailableOnce(t, uuid), func(t *testing.T, err error, api, target *fakeServer) {
			// A lookup the server could not answer is retried, as discovery is.
			require.NoError(t, err)
			require.Len(t, target.requests(), 1)
			paths := make([]string, 0, 4)
			for _, request := range api.requests() {
				paths = append(paths, request.Path)
			}
			require.Equal(t, []string{
				"/images/my-image/build-logs-url", "/images/my-image",
				"/images/my-image/build-logs-url", "/images/my-image",
			}, paths)
		}},
		{"not found", func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusNotFound)
			writeJSON(t, w, map[string]string{"code": "IMAGE_NOT_FOUND", "source": "DAYTONA_API", "message": "image not found"})
		}, func(t *testing.T, err error, _, target *fakeServer) {
			// The lookup's HTTP error is the answer, as a discovery error is.
			require.ErrorIs(t, err, ErrNotFound)
			var interrupted *BuildLogInterruptedError
			require.NotErrorAs(t, err, &interrupted)
			require.Empty(t, target.requests())
		}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given discovery that answers a name with the image's canonical ID.
			target := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /build-logs/images/{id}": buildLogsSSE(t, buildLogsDone("empty")),
			})
			lookup := tt.lookup
			if lookup == nil {
				lookup = jsonHandler(t, map[string]string{"id": uuid})
			}
			api := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /images/my-image/build-logs-url": jsonHandler(t, map[string]string{
					"url": target.Server.URL + "/build-logs/images/" + uuid,
				}),
				"GET /images/my-image": lookup,
			})
			client := newTestClient(t, api.Server)
			// When following the image by name.
			_, err := buildLogsCollect(client.Images().FollowBuildLogs(t.Context(), "my-image", nil))
			// Then the destination is held to the ID the name resolves to.
			tt.check(t, err, api, target)
		})
	}
}

func unavailableOnce(t *testing.T, id string) http.HandlerFunc {
	var calls atomic.Int32
	return func(w http.ResponseWriter, _ *http.Request) {
		if calls.Add(1) == 1 {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusServiceUnavailable)
			writeJSON(t, w, map[string]string{"code": "SERVICE_UNAVAILABLE"})
			return
		}
		writeJSON(t, w, map[string]string{"id": id})
	}
}
