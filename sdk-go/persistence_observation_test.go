// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestFork_whenWaitingDetailsOmitKnownPersistence(t *testing.T) {
	persistent, ephemeral := true, false
	for _, tc := range []struct {
		name         string
		source, fork *bool
		want         bool
	}{
		{"both omitted", nil, nil, true},
		{"source reports false", &ephemeral, nil, false},
		{"fork reports false", nil, &ephemeral, false},
		{"fork reports true", &ephemeral, &persistent, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given known source persistence and optional detail observations.
			sourceDetail := sandboxFixture("source")
			if tc.source != nil {
				sourceDetail["persistent"] = *tc.source
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/source":       jsonHandler(t, sourceDetail),
				"POST /sandboxes/source/fork": jsonHandler(t, map[string]string{"id": "fork", "status": "pending"}),
				"GET /sandboxes/fork": func(w http.ResponseWriter, r *http.Request) {
					detail := sandboxFixture("fork")
					if r.URL.Query().Get("wait") == "" {
						detail["status"] = "pending"
						if tc.fork != nil {
							detail["persistent"] = *tc.fork
						}
					}
					writeJSON(t, w, detail)
				},
			})
			source := newForkTestSource(t, fake)
			source.info.Persistent = &persistent
			// When the source refresh, fork refresh and readiness hold replace metadata.
			fork, err := source.Fork(t.Context(), nil)
			// Then omissions retain known persistence and explicit booleans win.
			require.NoError(t, err)
			require.Equal(t, SandboxStateStarted, fork.State())
			require.NotNil(t, fork.Info().Persistent)
			require.Equal(t, tc.want, *fork.Info().Persistent)
		})
	}
}

func TestCreate_whenWaitDetailsOmitKnownPersistence(t *testing.T) {
	// Given a persistent create accepted before it is running.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes":        jsonHandler(t, map[string]string{"id": "created", "status": "pending"}),
		"GET /sandboxes/created": jsonHandler(t, sandboxFixture("created")),
	})
	client := newTestClient(t, fake.Server)
	persistent := true
	// When the readiness read omits optional persistence.
	sandbox, err := client.Create(t.Context(), "", &CreateOptions{Persistent: &persistent})
	// Then the accepted create's known persistence survives the readiness wait.
	require.NoError(t, err)
	require.Equal(t, SandboxStateStarted, sandbox.State())
	require.NotNil(t, sandbox.Info().Persistent)
	require.True(t, *sandbox.Info().Persistent)
}
