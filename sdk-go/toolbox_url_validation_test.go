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
		"https://toolbox.invalid:99999/tools", "https://toolbox.invalid/s bx", "https://toolbox.invalid/s\tbx",
		"https://toolbox.invalid:99999/{sandboxId}",
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
	for endpoint, want := range map[string]string{
		"https://toolbox.invalid:65535/tools": "https://toolbox.invalid:65535/tools",
		"http://[::1]:0/tools":                "http://[::1]:0/tools",
		"HTTP://127.0.0.1:8080/{sandboxId}":   "HTTP://127.0.0.1:8080/sbx",
		"https://user@toolbox.invalid/tools/": "https://user@toolbox.invalid/tools",
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
			// The override, not the API's toolbox URL, is where toolbox calls go.
			require.Equal(t, want, sandbox.toolboxURL)
		})
	}
}

// One toolbox URL override rule in every SDK: the same lists are in
// sdk-python/tests/test_config_validation.py and sdk-typescript/tests/config-validation.test.ts.
var sameRuleRefused = []string{
	"http://%C3%A9.example/x",
	"http://fe80::1/x",
	"http://[xyz]/x",
	"https://user\\x@toolbox.example/x",
	"https://toolbox.example/tools\u0085x",
	"https://toolbox.example/tools\u0080x",
	"https://toolbox.example/tools\u009fx",
	"https://toolbox.example/tools\ufeffx",
	"https://toolbox.example/tools\u00a0x",
	"https://toolbox.example/tools\u3000x",
	"https://toolbox\u0080.example/tools",
	"https://toolbox\ufeff.example/tools",
	"http://a|b.example/x",
	"http://a<b.example/x",
	"http://a\"b.example/x",
	"http://a^b.example/x",
	"http://a{b.example/x",
	"http://[fe80::1%25eth0]:8080/x",
	"http://%41.example/x",
	"https://toolbox.example:99999/x",
	"https://h:+80/x",
}

var sameRuleAccepted = []string{
	"https://toolbox.example/tools%20x",
	"http://my_host.example/x",
	"http://xn--bcher-kva.example/x",
	"http://b\u00fccher.example/x",
	"http://[::1]:8080/x",
	"https://user@toolbox.example/x",
	"https://toolbox.example:65535/{sandboxId}",
	"http://127.0.0.1/x",
}

func TestCheckToolboxOverride_followsTheRuleSharedWithPythonAndTypeScript(t *testing.T) {
	for _, endpoint := range sameRuleRefused {
		t.Run("refuses "+endpoint, func(t *testing.T) {
			// When
			err := checkToolboxOverride(endpoint)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
	for _, endpoint := range sameRuleAccepted {
		t.Run("accepts "+endpoint, func(t *testing.T) {
			// When
			_, scopeErr := scopedToolboxURL("", "sbx", endpoint)
			// Then
			require.NoError(t, checkToolboxOverride(endpoint))
			require.NoError(t, scopeErr)
		})
	}
}

func TestGet_whenDirectToolboxURLIsInvalidUTF8(t *testing.T) {
	for _, endpoint := range []string{
		"https://toolbox.invalid/tools\xff", "https://toolbox\xc3.invalid/tools", "https://toolbox.invalid/\xed\xa0\x80",
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
			require.ErrorContains(t, err, "ToolboxURL must be valid UTF-8")
			require.Empty(t, fake.requests())
		})
	}
}
