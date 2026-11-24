// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"testing"

	"github.com/stretchr/testify/require"
)

type sandboxJSONFixture struct {
	Detail          json.RawMessage `json:"detail"`
	HandleJSON      json.RawMessage `json:"handleJson"`
	CreateResponses []struct {
		Call       string          `json:"call"`
		Source     string          `json:"source"`
		Region     string          `json:"region"`
		Response   json.RawMessage `json:"response"`
		HandleJSON json.RawMessage `json:"handleJson"`
	} `json:"createResponses"`
}

func readSandboxJSONFixture(t *testing.T) sandboxJSONFixture {
	t.Helper()
	data, err := os.ReadFile("../sdk-python/tests/fixtures/sandbox-handle-json.json")
	if errors.Is(err, os.ErrNotExist) && os.Getenv("DAYTONA_SDK_GO_REQUIRE_FIXTURES") == "" {
		t.Skip("shared fixture is absent from the published module")
	}
	require.NoError(t, err)
	var fixture sandboxJSONFixture
	require.NoError(t, json.Unmarshal(data, &fixture))
	return fixture
}

func TestSandboxToJSON_whenDetailRead_matchesSharedFixtureByteForByte(t *testing.T) {
	// Given
	fixture := readSandboxJSONFixture(t)
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx-ts": jsonHandler(t, fixture.Detail),
	})
	client := newTestClient(t, fake.Server)
	sandbox, err := client.Get(t.Context(), "sbx-ts", nil)
	require.NoError(t, err)
	// When
	encoded, err := json.Marshal(sandbox.ToJSON())
	// Then: canonical bytes preserve every timestamp digit and omission.
	require.NoError(t, err)
	require.Equal(t, canonicalSandboxJSON(t, fixture.HandleJSON), canonicalSandboxJSON(t, encoded))
	var restored SandboxHandleJSON
	require.NoError(t, json.Unmarshal(encoded, &restored))
	roundTrip, err := json.Marshal(restored)
	require.NoError(t, err)
	require.Equal(t, encoded, roundTrip)
}

func TestSandboxToJSON_whenCreateDoesNotWait_matchesSharedFixture(t *testing.T) {
	fixture := readSandboxJSONFixture(t)
	for _, test := range fixture.CreateResponses {
		t.Run(test.Call+"/"+test.Source, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes":               jsonHandler(t, test.Response),
				"POST /sandboxes/from-snapshot": jsonHandler(t, test.Response),
				"POST /sandboxes/macos":         jsonHandler(t, test.Response),
			})
			client := newTestClient(t, fake.Server)
			var sandbox *Sandbox
			var err error
			switch test.Call {
			case "create":
				sandbox, err = client.Create(t.Context(), test.Source, &CreateOptions{Wait: NoWait, Region: test.Region})
			case "createFromSnapshot":
				sandbox, err = client.CreateFromSnapshot(t.Context(), test.Source, &CreateFromSnapshotOptions{Wait: NoWait})
			case "createMacos":
				sandbox, err = client.CreateMacOS(t.Context(), test.Source, &CreateMacOSOptions{Wait: NoWait})
			default:
				t.Fatalf("unknown fixture operation %q", test.Call)
			}
			require.NoError(t, err)
			// When
			encoded, err := json.Marshal(sandbox.ToJSON())
			// Then
			require.NoError(t, err)
			require.Equal(t, canonicalSandboxJSON(t, test.HandleJSON), canonicalSandboxJSON(t, encoded))
			require.Len(t, fake.requests(), 1)
		})
	}
}

func canonicalSandboxJSON(t *testing.T, data []byte) string {
	t.Helper()
	var value map[string]json.RawMessage
	require.NoError(t, json.Unmarshal(data, &value))
	// Recurse through objects; arrays are not present in SandboxHandleJSON.
	for key, raw := range value {
		if len(raw) > 0 && raw[0] == '{' {
			value[key] = json.RawMessage(canonicalSandboxJSON(t, raw))
		}
	}
	encoded, err := json.Marshal(value)
	require.NoError(t, err)
	return string(encoded)
}
