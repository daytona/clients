// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func newForkTestSource(t *testing.T, fake *fakeServer) *Sandbox {
	t.Helper()
	client := newTestClient(t, fake.Server)
	sandbox, err := client.newSandbox(sandboxInit{
		Info:    SandboxInfo{ID: "source", Class: SandboxClassLinux, RawClass: "linux", State: SandboxStateStarted},
		Options: GetSandboxOptions{ToolboxURL: fake.Server.URL + "/direct-source", AccessToken: "source-" + "credential"},
	})
	require.NoError(t, err)
	return sandbox
}

func TestForkNoWaitPostsOnceWithoutInheritingCredentials(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/source/fork": jsonHandler(t, map[string]any{
			"id": "fork", "status": "pending", "toolbox_proxy_url": "https://fork.example.com",
		}),
	})
	sandbox := newForkTestSource(t, fake)
	sandbox.info.Class, sandbox.info.RawClass = SandboxClassUnknown, "future-class"
	memory := false
	// When
	fork, err := sandbox.Fork(t.Context(), &ForkOptions{Name: "copy", Memory: &memory, Wait: NoWait})
	// Then
	require.NoError(t, err)
	require.Len(t, fake.requests(), 1)
	require.JSONEq(t, `{"name":"copy","memory":false}`, string(fake.requests()[0].Body))
	require.Empty(t, fake.requests()[0].Header.Get("Idempotency-Key"))
	require.Equal(t, SandboxStateCreating, fork.State())
	require.Equal(t, "source", fork.Info().ForkedFrom)
	require.Equal(t, "future-class", fork.Info().RawClass)
	require.Empty(t, fork.auth.cache.snapshot().token)
	require.True(t, fork.auth.mintable)
	require.False(t, fork.explicitToolboxURL)
	require.Equal(t, "https://fork.example.com/fork", fork.toolboxURL)
	require.Nil(t, fork.Info().Labels)
}

func TestForkWaitUsesFreshSourceStateAndClass(t *testing.T) {
	diskOnly := false
	for _, tc := range []struct {
		class   string
		source  string
		memory  *bool
		interim string
		final   string
	}{
		{"linux", "running", nil, "paused", "running"},
		{"linux", "running", &diskOnly, "stopped", "running"},
		{"linux", "paused", nil, "stopped", "paused"},
		{"linux", "paused", &diskOnly, "paused", "stopped"},
		{"linux", "stopped", nil, "paused", "stopped"},
		{"macos", "running", nil, "stopped", "running"},
		{"macos", "paused", nil, "pending", "stopped"},
		{"macos", "stopped", nil, "pending", "stopped"},
	} {
		t.Run(tc.class+"/"+tc.source+"/"+tc.final, func(t *testing.T) {
			// Given
			source := sandboxFixture("source")
			source["class"], source["status"] = tc.class, tc.source
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/source":       jsonHandler(t, source),
				"POST /sandboxes/source/fork": jsonHandler(t, map[string]string{"id": "fork", "status": "pending"}),
				"GET /sandboxes/fork": func(w http.ResponseWriter, r *http.Request) {
					detail := sandboxFixture("fork")
					detail["class"], detail["status"] = tc.class, tc.interim
					if r.URL.Query().Get("wait") != "" {
						detail["status"] = tc.final
					}
					writeJSON(t, w, detail)
				},
			})
			sandbox := newForkTestSource(t, fake)
			sandbox.info.State = SandboxStateStopped
			// When
			fork, err := sandbox.Fork(t.Context(), &ForkOptions{Memory: tc.memory})
			// Then
			require.NoError(t, err)
			require.Equal(t, sandboxState(tc.final), fork.State())
			require.Equal(t, "source", fork.Info().ForkedFrom)
			require.Len(t, fake.requests(), 4)
			require.Equal(t, http.MethodGet, fake.requests()[0].Method)
			require.Empty(t, fake.requests()[2].Query)
			require.Equal(t, tc.final, fake.requests()[3].Query.Get("wait"))
		})
	}
}

func TestForkPreservesPOSTToolboxAndDetailProvenance(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/source": jsonHandler(t, sandboxFixture("source")),
		"POST /sandboxes/source/fork": jsonHandler(t, map[string]string{
			"id": "fork", "forked_from": "post-source", "toolbox_proxy_url": "https://accepted.example.com",
		}),
		"GET /sandboxes/fork": func(w http.ResponseWriter, _ *http.Request) {
			detail := sandboxFixture("fork")
			detail["toolbox_proxy_url"], detail["forked_from"] = "https://detail.example.com", "detail-source"
			writeJSON(t, w, detail)
		},
	})
	sandbox := newForkTestSource(t, fake)
	// When
	fork, err := sandbox.Fork(t.Context(), nil)
	// Then
	require.NoError(t, err)
	require.Equal(t, "https://accepted.example.com/fork", fork.toolboxURL)
	require.Equal(t, "detail-source", fork.Info().ForkedFrom)
}

func TestForkValidationPrecedesSourceRead(t *testing.T) {
	for name, opts := range map[string]*ForkOptions{
		"blank name": {Name: "\x1c"}, "invalid UTF-8": {Name: string([]byte{0xff})},
		"negative wait": {Wait: -time.Second}, "wrong sentinel": {Wait: NoTimeout},
	} {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox := newForkTestSource(t, fake)
			// When
			_, err := sandbox.Fork(t.Context(), opts)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestForkRefusesUnknownClassesBeforeSubmission(t *testing.T) {
	for _, knownUnknown := range []bool{false, true} {
		t.Run(map[bool]string{false: "new observation", true: "known unknown"}[knownUnknown], func(t *testing.T) {
			// Given
			detail := sandboxFixture("source")
			detail["class"] = "future-class"
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/source": jsonHandler(t, detail)})
			sandbox := newForkTestSource(t, fake)
			if knownUnknown {
				sandbox.info.Class, sandbox.info.RawClass = SandboxClassUnknown, "future-class"
			}
			// When
			_, err := sandbox.Fork(t.Context(), nil)
			// Then
			require.ErrorIs(t, err, ErrUnsupportedOperation)
			if knownUnknown {
				require.Empty(t, fake.requests())
			} else {
				require.Len(t, fake.requests(), 1)
				require.Equal(t, http.MethodGet, fake.requests()[0].Method)
			}
		})
	}
}
