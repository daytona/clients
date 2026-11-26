// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestCreate_whenPersistenceIsSelected(t *testing.T) {
	persistent, ephemeral := true, false
	for _, tc := range []struct {
		name  string
		value *bool
	}{
		{"default", nil}, {"persistent", &persistent}, {"ephemeral", &ephemeral},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given an accepted create without a detail read or persistence echo.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes": jsonHandler(t, map[string]string{"id": "created"}),
			})
			client := newTestClient(t, fake.Server)
			// When persistence is selected or omitted.
			sandbox, err := client.Create(t.Context(), "", &CreateOptions{Persistent: tc.value, Wait: NoWait})
			// Then both the request and known metadata preserve absence versus false.
			require.NoError(t, err)
			var body map[string]json.RawMessage
			require.NoError(t, json.Unmarshal(fake.requests()[0].Body, &body))
			if tc.value == nil {
				require.NotContains(t, body, "persistent")
			} else {
				var got bool
				require.NoError(t, json.Unmarshal(body["persistent"], &got))
				require.Equal(t, *tc.value, got)
			}
			require.Equal(t, tc.value, sandbox.Info().Persistent)
			require.Equal(t, tc.value, sandbox.ToJSON().Persistent)
		})
	}
}

func TestCreate_whenPersistenceIsEchoed(t *testing.T) {
	// Given an accepted create with server-reported persistence.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes": jsonHandler(t, map[string]any{"id": "created", "persistent": true}),
	})
	client := newTestClient(t, fake.Server)
	// When create uses platform defaults.
	sandbox, err := client.Create(t.Context(), "", &CreateOptions{Wait: NoWait})
	// Then the accepted handle retains the known observation.
	require.NoError(t, err)
	require.NotNil(t, sandbox.Info().Persistent)
	require.True(t, *sandbox.Info().Persistent)
}

func TestCreateFromSnapshot_whenPersistenceIsInherited(t *testing.T) {
	for _, action := range []LifecycleAction{LifecycleActionStop, LifecycleActionPause} {
		t.Run(string(action), func(t *testing.T) {
			// Given source persistence is unknown until the API restores it.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes/from-snapshot": jsonHandler(t, map[string]any{"id": "restored", "persistent": true}),
			})
			client := newTestClient(t, fake.Server)
			// When requesting a valid policy for a persistent snapshot.
			sandbox, err := client.CreateFromSnapshot(t.Context(), "snap", &CreateFromSnapshotOptions{
				Wait: NoWait, LifecyclePolicy: &LifecyclePolicyOptions{OnTimeout: action},
			})
			// Then no persistence override is sent and the API's observation is retained.
			require.NoError(t, err)
			var body map[string]json.RawMessage
			require.NoError(t, json.Unmarshal(fake.requests()[0].Body, &body))
			require.NotContains(t, body, "persistent")
			require.NotNil(t, sandbox.Info().Persistent)
			require.True(t, *sandbox.Info().Persistent)
		})
	}
}

func TestFork_whenPersistenceIsKnown(t *testing.T) {
	// Given an accepted fork with no detail read.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/source/fork": jsonHandler(t, map[string]string{"id": "fork"}),
	})
	source := newForkTestSource(t, fake)
	persistent := true
	source.info.Persistent = &persistent
	// When forking without a readiness wait.
	fork, err := source.Fork(t.Context(), &ForkOptions{Wait: NoWait})
	// Then source persistence survives on the accepted handle as an owned copy.
	require.NoError(t, err)
	persistent = false
	require.NotNil(t, fork.Info().Persistent)
	require.True(t, *fork.Info().Persistent)
}
