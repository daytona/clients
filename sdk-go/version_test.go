// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"runtime/debug"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestVersionHeaders_whenModuleBuildInfoVaries(t *testing.T) {
	const module = "go.daytona.com/sdk/v2"
	for _, tc := range []struct {
		name string
		info debug.BuildInfo
		want string
	}{
		{"main", debug.BuildInfo{Main: debug.Module{Path: module, Version: "v2.0.0-alpha.1"}}, "2.0.0-alpha.1"},
		{"dependency", debug.BuildInfo{Deps: []*debug.Module{{Path: module, Version: "v2.1.0"}}}, "2.1.0"},
		{"replacement", debug.BuildInfo{Deps: []*debug.Module{{Path: module, Version: "v2.1.0", Replace: &debug.Module{Version: "v2.2.0"}}}}, "2.2.0"},
		{"local replacement", debug.BuildInfo{Deps: []*debug.Module{{Path: module, Version: "v2.1.0", Replace: &debug.Module{Path: "../sdk"}}}}, "(devel)"},
		{"development", debug.BuildInfo{Main: debug.Module{Path: module, Version: "(devel)"}}, "(devel)"},
		{"unknown", debug.BuildInfo{}, "unknown"},
		{"empty version", debug.BuildInfo{Deps: []*debug.Module{{Path: module}}}, "unknown"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given: module metadata normally supplied by debug.ReadBuildInfo.
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /": jsonHandler(t, struct{}{})})
			client := newTestClient(t, fake.Server)
			client.version = versionFromBuildInfo(&tc.info)
			// When
			err := client.api().do(t.Context(), apiRequest{Path: "/"})
			// Then
			require.NoError(t, err)
			request := fake.requests()[0]
			require.Equal(t, tc.want, request.Header.Get("X-Daytona-SDK-Version"))
			require.Equal(t, "sdk-go/"+tc.want, request.Header.Get("User-Agent"))
		})
	}
}
