// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestToolbox_whenMintingOrUsingCallerToken(t *testing.T) {
	for _, test := range []struct {
		name, caller string
		mintStatus   int
		expected     string
	}{
		{"mint", "", 200, "minted"},
		{"caller", "caller", 200, "caller"},
		{"unsupported fallback", "", 405, "test-key"},
		{"missing fallback", "", 404, "test-key"},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes/sbx/access-token": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(test.mintStatus)
					writeJSON(t, w, map[string]any{
						"access_token": "minted", "expires_at": "malformed", "token_type": "Bearer", "operations": []string{"toolbox"},
					})
				},
				"GET /toolbox/sbx/files": jsonHandler(t, struct{}{}),
			})
			client := newTestClient(t, fake.Server)
			sandbox, err := client.newSandbox(sandboxInit{
				Info: SandboxInfo{ID: "sbx"}, ToolboxURL: fake.Server.URL + "/toolbox",
				Options: GetOptions{AccessToken: test.caller},
			})
			require.NoError(t, err)
			// When
			err = sandbox.toolbox().do(t.Context(), toolboxRequest{Path: "/files"})
			// Then
			require.NoError(t, err)
			requests := fake.requests()
			require.Equal(t, "Bearer "+test.expected, requests[len(requests)-1].Header.Get("Authorization"))
			if test.caller != "" {
				require.Len(t, requests, 1)
			} else {
				require.Len(t, requests, 2)
			}
		})
	}
}

func TestToolbox_whenUnauthorizedRetriesOnce(t *testing.T) {
	// Given
	var mints atomic.Int32
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/sbx/access-token": func(w http.ResponseWriter, _ *http.Request) {
			token := "initial"
			if mints.Add(1) > 1 {
				token = "refreshed"
			}
			writeJSON(t, w, map[string]any{"access_token": token, "expires_at": "2040-01-01T00:00:00Z", "token_type": "Bearer", "operations": []string{}})
		},
		"GET /toolbox/sbx/files": func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(401)
			writeJSON(t, w, map[string]string{"code": "UNAUTHORIZED", "source": SourceProxy})
		},
	})
	client := newTestClient(t, fake.Server)
	sandbox, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "sbx"}, ToolboxURL: fake.Server.URL + "/toolbox"})
	require.NoError(t, err)
	// When
	err = sandbox.toolbox().do(t.Context(), toolboxRequest{Path: "/files"})
	// Then
	require.ErrorIs(t, err, ErrAuthentication)
	require.EqualValues(t, 2, mints.Load())
	require.Len(t, fake.requests(), 4)
}

func TestToolbox_whenURLIsScoped(t *testing.T) {
	for _, test := range []struct {
		base, direct, id, want string
		bad                    bool
	}{
		{"https://h/toolbox/", "", "a/b", "https://h/toolbox/a%2Fb", false},
		{"https://h/{sandboxId}", "", "a b!*", "https://h/a%20b%21%2A", false},
		{"https://h", "https://direct/tools", "sbx", "https://direct/tools", false},
		{"", "https://direct/{sandbox_id}", "sbx", "https://direct/sbx", false},
		{"https://{sandboxId}.example", "", "sbx", "", true},
		{"https://h?x=/{sandboxId}", "", "sbx", "", true},
		{"https://h/#{sandboxId}", "", "sbx", "", true},
	} {
		t.Run(test.base+test.direct, func(t *testing.T) {
			// Given/When
			actual, err := scopedToolboxURL(test.base, test.id, test.direct)
			// Then
			if test.bad {
				require.ErrorIs(t, err, ErrBadRequest)
				return
			}
			require.NoError(t, err)
			require.Equal(t, test.want, actual)
		})
	}
}

func TestStream_whenTransferStalls(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /stream": func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
		if err := http.NewResponseController(w).Flush(); err != nil {
			t.Error(err)
			return
		}
		<-r.Context().Done()
	}})
	client := newTestClient(t, fake.Server, WithRequestTimeout(20*time.Millisecond))
	response, err := client.api().stream(t.Context(), apiRequest{Path: "/stream", Transfer: true})
	require.NoError(t, err)
	defer func() { require.NoError(t, response.Body.Close()) }()
	// When
	_, err = io.ReadAll(response.Body)
	// Then
	require.ErrorIs(t, err, ErrClientTimeout)
	require.ErrorIs(t, err, context.DeadlineExceeded)
}

func TestStream_whenBodyExceedsBufferedLimit(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /stream": func(w http.ResponseWriter, _ *http.Request) {
		if _, err := io.WriteString(w, strings.Repeat("x", 100)); err != nil {
			t.Error(err)
		}
	}})
	client := newTestClient(t, fake.Server, WithMaxResponseBytes(1))
	sandbox, err := client.newSandbox(sandboxInit{
		Info:    SandboxInfo{ID: "sbx"},
		Options: GetOptions{ToolboxURL: fake.Server.URL, AccessToken: "caller"},
	})
	require.NoError(t, err)
	// When
	response, err := sandbox.toolbox().stream(t.Context(), toolboxRequest{Path: "/stream", Transfer: true})
	require.NoError(t, err)
	defer func() { require.NoError(t, response.Body.Close()) }()
	body, err := io.ReadAll(response.Body)
	// Then
	require.NoError(t, err)
	require.Len(t, body, 100)
}

func TestToolbox_whenResolvingConnectionInfo(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/sbx/access-token": jsonHandler(t, map[string]any{
			"access_token": "minted", "expires_at": "2040-01-01T00:00:00Z",
			"token_type": "Bearer", "operations": []string{"toolbox"},
		}),
	})
	client := newTestClient(t, fake.Server)
	sandbox, err := client.newSandbox(sandboxInit{
		Info: SandboxInfo{ID: "sbx"}, ToolboxURL: fake.Server.URL + "/toolbox",
	})
	require.NoError(t, err)
	// When
	endpoint, headers, err := sandbox.toolbox().connectionInfo(t.Context())
	// Then
	require.NoError(t, err)
	require.Equal(t, fake.Server.URL+"/toolbox/sbx", endpoint)
	require.Equal(t, "Bearer minted", headers.Get("Authorization"))
	require.Equal(t, "sdk-go", headers.Get("X-Daytona-Source"))
	require.Equal(t, "minted", sandbox.Info().AccessToken)
	require.Len(t, fake.requests(), 1)
}
