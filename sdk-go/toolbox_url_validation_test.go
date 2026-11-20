// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestGet_whenDirectToolboxURLIsMalformed(t *testing.T) {
	for _, endpoint := range []string{
		"toolbox.invalid/tools", "ftp://toolbox.invalid/tools", "http:/tools", "https://",
		"https:opaque/{sandboxId}", "https://{sandboxId}@toolbox.invalid/tools",
		"https://user:{sandbox_id}@toolbox.invalid/tools",
		"https://toolbox.invalid/tools?route=one", "https://toolbox.invalid/tools#anchor",
	} {
		t.Run(endpoint, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx": jsonHandler(t, sandboxFixture("sbx")),
			})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Get(t.Context(), "sbx", &GetSandboxOptions{ToolboxURL: endpoint})
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestSandbox_whenProxyToolboxURLHasQueryOrFragment(t *testing.T) {
	for _, suffix := range []string{"?route=one", "#anchor", "?", "#"} {
		t.Run(suffix, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.newSandbox(sandboxInit{
				Info: SandboxInfo{ID: "sbx"}, ToolboxURL: fake.Server.URL + "/toolbox" + suffix,
			})
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}
