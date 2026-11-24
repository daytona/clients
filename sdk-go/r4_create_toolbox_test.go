// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestCreate_whenAcceptedToolboxURLUnusable_preservesHandle(t *testing.T) {
	for _, route := range createTestCases() {
		for _, endpoint := range unusableCreateToolboxURLs() {
			t.Run(route.name+"/"+endpoint, func(t *testing.T) {
				// Given
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"POST " + route.path: jsonHandler(t, map[string]string{
						"id": "accepted", "status": "pending", "toolbox_proxy_url": endpoint,
					}),
				})
				client := newTestClient(t, fake.Server)
				// When
				sandbox, err := route.call(t.Context(), client, &CreateFromSnapshotOptions{Wait: NoWait})
				// Then
				require.NoError(t, err)
				require.Equal(t, "accepted", sandbox.ID())
				require.Equal(t, SandboxStateCreating, sandbox.State())
				require.Empty(t, sandbox.toolboxURL)
			})
		}
	}
}

func TestFork_whenAcceptedToolboxURLUnusable_preservesHandle(t *testing.T) {
	for _, endpoint := range unusableCreateToolboxURLs() {
		t.Run(endpoint, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes/source/fork": jsonHandler(t, map[string]string{
					"id": "accepted", "status": "pending", "toolbox_proxy_url": endpoint,
				}),
			})
			source := newForkTestSource(t, fake)
			// When
			sandbox, err := source.Fork(t.Context(), &ForkOptions{Wait: NoWait})
			// Then
			require.NoError(t, err)
			require.Equal(t, "accepted", sandbox.ID())
			require.Equal(t, SandboxStateCreating, sandbox.State())
			require.Empty(t, sandbox.toolboxURL)
		})
	}
}

func TestGet_whenToolboxURLUnusable_remainsStrict(t *testing.T) {
	for _, endpoint := range unusableCreateToolboxURLs() {
		t.Run(endpoint, func(t *testing.T) {
			// Given
			detail := sandboxFixture("sbx")
			detail["toolbox_proxy_url"] = endpoint
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx": jsonHandler(t, detail),
			})
			client := newTestClient(t, fake.Server)
			// When
			sandbox, err := client.Get(t.Context(), "sbx", nil)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Nil(t, sandbox)
		})
	}
}

func unusableCreateToolboxURLs() []string {
	return []string{
		"https://toolbox.invalid/x?query=1", "https://toolbox.invalid/x#fragment",
		"ftp://toolbox.invalid", "https://{sandboxId}.invalid/x",
	}
}
