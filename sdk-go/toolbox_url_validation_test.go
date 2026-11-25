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
		// Refused by the Python and TypeScript SDKs too.
		"https://toolbox.invalid:99999/tools", "https://toolbox.invalid/s bx", "https://toolbox.invalid/s\tbx",
		"https://toolbox.invalid/tools\u0085", "http://[fe80::1%25eth0]:8080/tools", "https://toolbox.invalid:99999/{sandboxId}",
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

func TestGet_whenDirectToolboxURLIsAccepted(t *testing.T) {
	for _, endpoint := range []string{
		"https://toolbox.invalid:65535/tools", "http://[::1]:0/tools", "HTTP://127.0.0.1:8080/{sandboxId}",
		"https://user@toolbox.invalid/tools",
	} {
		t.Run(endpoint, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx": jsonHandler(t, sandboxFixture("sbx")),
			})
			client := newTestClient(t, fake.Server)
			// When
			sandbox, err := client.Get(t.Context(), "sbx", &GetSandboxOptions{ToolboxURL: endpoint})
			// Then
			require.NoError(t, err)
			require.Equal(t, "sbx", sandbox.ID())
		})
	}
}
