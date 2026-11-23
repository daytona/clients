// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSandboxToJSON_whenDetailReportsEmptyValues_preservesPresence(t *testing.T) {
	// Given
	detail := sandboxFixture("sbx")
	detail["name"], detail["cpu"], detail["memory_mib"], detail["disk_mib"] = "", 0, 0, 0
	detail["memory_max_mib"], detail["disk_max_mib"], detail["persistent"] = 2048, 4096, false
	detail["class"], detail["status"] = "future-class", "future-status"
	detail["image"] = map[string]string{"id": "img_1", "name": ""}
	detail["snapshot"] = map[string]string{"id": "snap_1"}
	for _, name := range []string{"user", "error", "forked_from", "reservation_id", "image_build_id"} {
		detail[name] = ""
	}
	detail["lifecycle_policy"] = map[string]any{
		"timeout_seconds": 0, "idle_expiry_seconds": 0,
		"on_timeout": "", "on_idle_timeout": "", "last_activity_source": "",
	}
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx": jsonHandler(t, detail)})
	client := newTestClient(t, fake.Server)
	sandbox, setupErr := client.Get(t.Context(), "sbx", nil)
	require.NoError(t, setupErr)

	// When
	encoded, err := json.Marshal(sandbox.ToJSON())

	// Then
	require.NoError(t, err)
	require.JSONEq(t, `{
		"sandboxId":"sbx", "name":"", "cpu":0, "cpuMax":2, "memory":0, "memoryMax":2,
		"disk":0, "diskMax":4, "labels":{}, "persistent":false, "region":"region-a",
		"sandboxClass":"unknown", "rawSandboxClass":"future-class", "state":"unknown", "rawState":"future-status",
		"image":{"id":"img_1","name":""}, "snapshot":{"id":"snap_1"},
		"user":"", "error":"", "forkedFrom":"", "reservationId":"", "imageBuildId":"",
		"lifecyclePolicy":{"timeoutSeconds":0,"idleExpirySeconds":0,"onTimeout":"","onIdleTimeout":"","lastActivitySource":""}
	}`, string(encoded))
}

func TestSandboxToJSON_whenSnapshotIsMutated_keepsHandleIndependent(t *testing.T) {
	// Given
	detail := sandboxFixture("sbx")
	detail["image"] = map[string]string{"id": "img_1"}
	detail["lifecycle_policy"] = map[string]any{"timeout_seconds": 10}
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx": jsonHandler(t, detail)})
	client := newTestClient(t, fake.Server)
	sandbox, setupErr := client.Get(t.Context(), "sbx", nil)
	require.NoError(t, setupErr)
	before, encodeErr := json.Marshal(sandbox.ToJSON())
	require.NoError(t, encodeErr)
	snapshot := sandbox.ToJSON()

	// When
	(*snapshot.Labels)["injected"] = "value"
	*snapshot.CPUMax = 99
	snapshot.Image.ID = "img_other"
	*snapshot.LifecyclePolicy.TimeoutSeconds = 99

	// Then
	after, err := json.Marshal(sandbox.ToJSON())
	require.NoError(t, err)
	require.Equal(t, before, after)
}

func TestSandboxToJSON_whenHandleHasNoObservations_omitsUnknownFields(t *testing.T) {
	// Given
	sandbox := &Sandbox{id: "sbx", info: SandboxInfo{State: SandboxStateUnknown}}
	// When
	encoded, err := json.Marshal(sandbox.ToJSON())
	// Then
	require.NoError(t, err)
	require.JSONEq(t, `{"sandboxId":"sbx"}`, string(encoded))
}
