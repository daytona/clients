// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestCreate_whenAllLinuxOptionsAreGiven(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes": jsonHandler(t, map[string]any{"id": "created", "status": "running"}),
	})
	client := newTestClient(t, fake.Server)
	ceiling, memoryMax, diskMax := 2.5, 2.25, 10.5
	spot, blocked, provision := false, true, 120*time.Second
	labels := map[string]string{"team": "sdk"}
	options := CreateOptions{
		Name: "worker", Labels: labels, Region: "region-a", Wait: NoWait, CreateTimeout: &provision,
		IdempotencyKey: "request-123", Env: map[string]string{"MODE": "test"}, User: "worker",
		CPUArch: CPUArchARM64, CPUType: "performance", Spot: &spot,
		Resources: &Resources{
			CPU: 1.25, CPUMax: &ceiling, MemoryGiB: 1.5, MemoryMaxGiB: &memoryMax,
			DiskGiB: 10, DiskMaxGiB: &diskMax,
		},
		Ports: []PortMapping{{Port: 8080}, {Port: 53, Protocol: "udp"}},
		Volumes: []VolumeMount{
			{VolumeID: "vol", MountPath: "/data", Subpath: "sub", ReadOnly: true},
			{VolumeID: "disk", MountPath: "/disk"},
		},
		Secrets: []string{"registry"},
		LifecyclePolicy: &LifecyclePolicyOptions{
			Timeout: time.Hour, OnTimeout: LifecycleActionDelete,
			IdleExpiry: 2 * time.Minute, OnIdleTimeout: LifecycleActionDelete,
		},
		NetworkPolicy: &NetworkPolicySpec{BlockAll: &blocked, AllowCIDRs: []string{"10.0.0.0/8"}},
	}

	// When
	sandbox, err := client.Create(t.Context(), "  img_base  ", &options)

	// Then
	require.NoError(t, err)
	require.Equal(t, "created", sandbox.ID())
	requests := fake.requests()
	require.Len(t, requests, 1)
	require.Equal(t, "request-123", requests[0].Header.Get("Idempotency-Key"))
	require.Equal(t, "region-a", requests[0].Header.Get("X-Daytona-Region"))
	require.JSONEq(t, `{
		"name":"worker","image":"img_base","labels":{"team":"sdk","name":"worker"},
		"region_id":"region-a","wait_seconds":0,"create_timeout_seconds":120,
		"env":{"MODE":"test"},"user":"worker","cpu_arch":"arm64","cpu_type":"performance","spot":false,
		"cpu":1.25,"cores_max":2.5,"memory_mib":1536,"memory_max_mib":2304,"disk_mib":10240,"disk_max_mib":10752,
		"ports":[{"port":8080,"proto":"tcp"},{"port":53,"proto":"udp"}],
		"volumes":[{"volume":"vol","mount_path":"/data","subpath":"sub","read_only":true},{"volume":"disk","mount_path":"/disk"}],
		"secrets":["registry"],"network_policy":{"block_all":true,"allow_cidrs":["10.0.0.0/8"]},
		"lifecycle_policy":{"timeout_seconds":3600,"on_timeout":"delete","idle_expiry_seconds":120,"on_idle_timeout":"delete"}
	}`, string(requests[0].Body))
	require.Equal(t, map[string]string{"team": "sdk"}, labels)
}

func TestCreateFromSnapshot_whenOptionsAreGiven(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/from-snapshot": jsonHandler(t, map[string]any{"id": "restored", "status": "running"}),
	})
	client := newTestClient(t, fake.Server)
	timeout := WaitForever

	// When
	sandbox, err := client.CreateFromSnapshot(t.Context(), " snap_base ", &CreateFromSnapshotOptions{
		Name: "restored", Labels: map[string]string{}, Region: "region-b", Wait: time.Second,
		Ports: []PortMapping{}, CreateTimeout: &timeout,
		LifecyclePolicy: &LifecyclePolicyOptions{IdleExpiry: NoTimeout},
	})

	// Then
	require.NoError(t, err)
	require.Equal(t, &SnapshotRef{ID: "snap_base"}, sandbox.Info().Snapshot)
	request := fake.requests()[0]
	require.Equal(t, "region-b", request.Header.Get("X-Daytona-Region"))
	require.Equal(t, "name:restored", request.Header.Get("Idempotency-Key"))
	require.JSONEq(t, `{"name":"restored","labels":{"name":"restored"},"region_id":"region-b",
		"snapshot":"snap_base","wait_seconds":1,"ports":[],"create_timeout_seconds":-1,
		"lifecycle_policy":{"idle_expiry_seconds":-1}}`, string(request.Body))
}

func TestCreateMacOS_whenOptionsAreGiven(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/macos": jsonHandler(t, map[string]any{
			"id": "mac", "status": "running", "reservation_id": "res", "class": "linux",
		}),
	})
	client := newTestClient(t, fake.Server)
	persistent, renew, provision := true, false, time.Duration(0)

	// When
	sandbox, err := client.CreateMacOS(t.Context(), " base-macos ", &CreateMacOSOptions{
		Name: "mac", Region: "region-c", Wait: NoWait, CreateTimeout: &provision,
		Persistent: &persistent, Reservation: "new", AutoRenew: &renew,
		Resources: &MacOSResources{CPU: 6, MemoryGiB: 8.5, DiskGiB: 80.5},
		Env:       map[string]string{}, Secrets: []string{}, Volumes: []VolumeMount{},
		Ports: []PortMapping{{Port: 5900}}, ClearNetworkPolicy: true,
		LifecyclePolicy: &LifecyclePolicyOptions{Timeout: time.Hour, OnTimeout: LifecycleActionStop},
	})

	// Then
	require.NoError(t, err)
	require.Equal(t, SandboxClassMacOS, sandbox.Info().Class)
	require.Equal(t, "res", sandbox.Info().ReservationID)
	require.JSONEq(t, `{"name":"mac","image":"base-macos","labels":{"name":"mac"},"region_id":"region-c",
		"wait_seconds":0,"create_timeout_seconds":0,
		"persistent":true,"reservation":"new","auto_renew":false,"cores":6,"memory_mib":8704,"disk_mib":82432,
		"env":{},"secrets":[],"volumes":[],"ports":[{"port":5900,"proto":"tcp"}],"network_policy":null,
		"lifecycle_policy":{"timeout_seconds":3600,"on_timeout":"stop"}}`, string(fake.requests()[0].Body))
}

func TestCreate_whenDefaultsAreUsed(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes": jsonHandler(t, map[string]any{"id": "sbx", "status": "running"}),
	})
	client := newTestClient(t, fake.Server)

	// When
	_, err := client.Create(t.Context(), "", nil)

	// Then
	require.NoError(t, err)
	request := fake.requests()[0]
	var body map[string]any
	require.NoError(t, json.Unmarshal(request.Body, &body))
	require.Len(t, body, 1)
	require.InDelta(t, 60, body["wait_seconds"], 0)
	require.Regexp(t, `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`,
		request.Header.Get("Idempotency-Key"))
}

func TestCreate_whenExplicitEmptyCollectionsAreGiven(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes": jsonHandler(t, map[string]any{"id": "sbx", "status": "running"}),
	})
	client := newTestClient(t, fake.Server)

	// When
	_, err := client.Create(t.Context(), "", &CreateOptions{
		IdempotencyKey: "empty", Wait: NoWait, Ports: []PortMapping{}, Volumes: []VolumeMount{},
		Secrets: []string{}, Env: map[string]string{}, Labels: map[string]string{},
		NetworkPolicy: &NetworkPolicySpec{},
	})

	// Then
	require.NoError(t, err)
	require.JSONEq(t, `{"wait_seconds":0,"ports":[],"volumes":[],
		"secrets":[],"env":{},"labels":{},"network_policy":{}}`, string(fake.requests()[0].Body))
}
