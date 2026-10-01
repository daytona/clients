// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package common

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	apiclient "go.daytona.com/api-client-go"
)

func TestAwaitSandboxStateQueueTimeoutError(t *testing.T) {
	queueTimedOutAt := "2026-09-29T10:00:00.000Z"
	state := apiclient.SANDBOXSTATE_DESTROYED
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(apiclient.Sandbox{
			Id:              "sb-1",
			State:           &state,
			QueueTimedOutAt: &queueTimedOutAt,
			QueueTimeout:    *apiclient.NewNullableInt32(apiclient.PtrInt32(9)),
		})
	}))
	defer server.Close()

	err := AwaitSandboxState(context.Background(), apiClientForStateTest(server.URL), "sb-1", apiclient.SANDBOXSTATE_STARTED)
	if err == nil {
		t.Fatal("expected queue-timeout error")
	}
	if err.Error() != "sandbox sb-1 was destroyed after waiting 9 minutes for a runner (queue timed out at 2026-09-29T10:00:00.000Z)" {
		t.Fatalf("error = %q", err)
	}
}

func TestAwaitSandboxStateQueueTimeoutErrorWithoutQueueTimeout(t *testing.T) {
	queueTimedOutAt := "2026-09-29T10:00:00.000Z"
	state := apiclient.SANDBOXSTATE_DESTROYED
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(apiclient.Sandbox{
			Id:              "sb-1",
			State:           &state,
			QueueTimedOutAt: &queueTimedOutAt,
		})
	}))
	defer server.Close()

	err := AwaitSandboxState(context.Background(), apiClientForStateTest(server.URL), "sb-1", apiclient.SANDBOXSTATE_STARTED)
	if err == nil {
		t.Fatal("expected queue-timeout error")
	}
	if err.Error() != "sandbox sb-1 was destroyed after waiting for a runner (queue timed out at 2026-09-29T10:00:00.000Z)" {
		t.Fatalf("error = %q", err)
	}
}

func TestAwaitSandboxStateQueueTimeoutErrorSingularMinute(t *testing.T) {
	queueTimedOutAt := "2026-09-29T10:00:00.000Z"
	state := apiclient.SANDBOXSTATE_DESTROYED
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(apiclient.Sandbox{
			Id:              "sb-1",
			State:           &state,
			QueueTimedOutAt: &queueTimedOutAt,
			QueueTimeout:    *apiclient.NewNullableInt32(apiclient.PtrInt32(1)),
		})
	}))
	defer server.Close()

	err := AwaitSandboxState(context.Background(), apiClientForStateTest(server.URL), "sb-1", apiclient.SANDBOXSTATE_STARTED)
	if err == nil {
		t.Fatal("expected queue-timeout error")
	}
	if err.Error() != "sandbox sb-1 was destroyed after waiting 1 minute for a runner (queue timed out at 2026-09-29T10:00:00.000Z)" {
		t.Fatalf("error = %q", err)
	}
}

func TestAwaitSandboxStateSpotEvictedError(t *testing.T) {
	spotEvictedAt := "2026-09-29T10:05:00.000Z"
	state := apiclient.SANDBOXSTATE_DESTROYED
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(apiclient.Sandbox{
			Id:            "sb-1",
			State:         &state,
			SpotEvictedAt: &spotEvictedAt,
		})
	}))
	defer server.Close()

	err := AwaitSandboxState(context.Background(), apiClientForStateTest(server.URL), "sb-1", apiclient.SANDBOXSTATE_STARTED)
	if err == nil {
		t.Fatal("expected spot-evicted error")
	}
	if err.Error() != "sandbox sb-1 was evicted by spot preemption at 2026-09-29T10:05:00.000Z" {
		t.Fatalf("error = %q", err)
	}
}

func TestAwaitSandboxStateDestroyedTargetStillSucceedsWithQueueTimeoutMarker(t *testing.T) {
	queueTimedOutAt := "2026-09-29T10:10:00.000Z"
	state := apiclient.SANDBOXSTATE_DESTROYED
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(apiclient.Sandbox{
			Id:              "sb-1",
			State:           &state,
			QueueTimedOutAt: &queueTimedOutAt,
			QueueTimeout:    *apiclient.NewNullableInt32(apiclient.PtrInt32(4)),
		})
	}))
	defer server.Close()

	if err := AwaitSandboxState(context.Background(), apiClientForStateTest(server.URL), "sb-1", apiclient.SANDBOXSTATE_DESTROYED); err != nil {
		t.Fatalf("AwaitSandboxState() error = %v", err)
	}
}

func apiClientForStateTest(serverURL string) *apiclient.APIClient {
	configuration := apiclient.NewConfiguration()
	configuration.Servers = apiclient.ServerConfigurations{{URL: serverURL}}
	return apiclient.NewAPIClient(configuration)
}
