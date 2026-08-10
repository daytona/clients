// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	stderrors "errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	apiclient "github.com/daytona/clients/api-client-go"
	sdkerrors "github.com/daytona/clients/sdk-go/pkg/errors"
	"github.com/daytona/clients/sdk-go/pkg/types"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestSnapshotServiceCreation(t *testing.T) {
	t.Setenv("DAYTONA_API_KEY", "test-api-key")
	t.Setenv("DAYTONA_API_URL", "")
	t.Setenv("DAYTONA_JWT_TOKEN", "")
	t.Setenv("DAYTONA_ORGANIZATION_ID", "")

	client, err := NewClient()
	require.NoError(t, err)

	ss := NewSnapshotService(client)
	require.NotNil(t, ss)
}

func TestSnapshotListError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": "internal error"})
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)

	ctx := context.Background()
	_, err := client.Snapshot.List(ctx, nil, nil)
	require.Error(t, err)
}

func TestSnapshotGetError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": "not found"})
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)

	ctx := context.Background()
	_, err := client.Snapshot.Get(ctx, "nonexistent")
	require.Error(t, err)
}

func TestSnapshotDeleteError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusForbidden)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": "forbidden"})
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)

	snap := &types.Snapshot{ID: "snap-1", Name: "my-snapshot"}
	ctx := context.Background()
	err := client.Snapshot.Delete(ctx, snap)
	require.Error(t, err)
}

func TestSnapshotErrorHandling(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": "snapshot not found"})
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)

	ctx := context.Background()
	_, err := client.Snapshot.Get(ctx, "nonexistent")
	require.Error(t, err)
}

func TestMapSnapshotFromAPI(t *testing.T) {
	apiSnapshot := apiclient.NewSnapshotDtoWithDefaults()
	apiSnapshot.SetId("snap-1")
	apiSnapshot.SetName("test-snapshot")
	apiSnapshot.SetState("active")
	apiSnapshot.SetGeneral(false)
	apiSnapshot.SetCpu(4)
	apiSnapshot.SetGpu(0)
	apiSnapshot.SetMem(8)
	apiSnapshot.SetDisk(30)
	apiSnapshot.SetOrganizationId("org-1")
	apiSnapshot.SetImageName("python:3.11")

	snapshot := mapSnapshotFromAPI(apiSnapshot)
	assert.Equal(t, "snap-1", snapshot.ID)
	assert.Equal(t, "test-snapshot", snapshot.Name)
	assert.Equal(t, "active", snapshot.State)
	assert.Equal(t, "org-1", snapshot.OrganizationID)
	assert.Equal(t, "python:3.11", snapshot.ImageName)
	assert.Equal(t, 4, snapshot.CPU)
	assert.Equal(t, 8, snapshot.Memory)
	assert.Equal(t, 30, snapshot.Disk)
}

func TestSnapshotSuccessOperations(t *testing.T) {
	t.Run("list and get map responses", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Method == http.MethodGet && r.URL.Query().Get("page") != "" {
				writeJSONResponse(t, w, http.StatusOK, map[string]any{"items": []any{testSnapshotPayload("snap-1", "first", apiclient.SNAPSHOTSTATE_ACTIVE)}, "total": 1, "page": 1, "totalPages": 1})
				return
			}
			writeJSONResponse(t, w, http.StatusOK, testSnapshotPayload("snap-1", "first", apiclient.SNAPSHOTSTATE_ACTIVE))
		}))
		defer server.Close()

		client := createTestClientWithServer(t, server)
		page, limit := 1, 10
		list, err := client.Snapshot.List(context.Background(), &page, &limit)
		require.NoError(t, err)
		assert.Len(t, list.Items, 1)
		snapshot, err := client.Snapshot.Get(context.Background(), "snap-1")
		require.NoError(t, err)
		assert.Equal(t, "snap-1", snapshot.ID)
	})

	t.Run("list with query sends the sourceSandboxId param", func(t *testing.T) {
		var gotSourceSandboxID string
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			gotSourceSandboxID = r.URL.Query().Get("sourceSandboxId")
			writeJSONResponse(t, w, http.StatusOK, map[string]any{"items": []any{testSnapshotPayload("snap-1", "first", apiclient.SNAPSHOTSTATE_ACTIVE)}, "total": 1, "page": 1, "totalPages": 1})
		}))
		defer server.Close()

		client := createTestClientWithServer(t, server)
		sourceSandboxID := "sandbox-1"
		list, err := client.Snapshot.ListWithQuery(context.Background(), &ListSnapshotsQuery{SourceSandboxID: &sourceSandboxID})
		require.NoError(t, err)
		assert.Len(t, list.Items, 1)
		assert.Equal(t, "sandbox-1", gotSourceSandboxID)
	})

	t.Run("create with image streams logs for active snapshot", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			switch {
			case r.Method == http.MethodPost:
				writeJSONResponse(t, w, http.StatusOK, testSnapshotPayload("snap-2", "created", apiclient.SNAPSHOTSTATE_ACTIVE))
			case strings.Contains(r.URL.Path, "build-logs"):
				_, _ = w.Write([]byte("build line 1\nbuild line 2\n"))
			default:
				writeJSONResponse(t, w, http.StatusOK, testSnapshotPayload("snap-2", "created", apiclient.SNAPSHOTSTATE_ACTIVE))
			}
		}))
		defer server.Close()

		client := createTestClientWithServer(t, server)
		snapshot, logChan, err := client.Snapshot.Create(context.Background(), &types.CreateSnapshotParams{Name: "created", Image: "python:3.12"})
		require.NoError(t, err)
		assert.Equal(t, "snap-2", snapshot.ID)
		logs := make([]string, 0, 4)
		for line := range logChan {
			logs = append(logs, line)
		}
		assert.NotEmpty(t, logs)
	})
}

func TestSnapshotLogStreamingHelpers(t *testing.T) {
	t.Run("streamLogsHTTP handles non-200 response", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusBadGateway)
		}))
		defer server.Close()

		client := createTestClientWithServer(t, server)
		service := client.Snapshot
		err := service.streamLogsHTTP(context.Background(), "snap-1", make(chan string, 1))
		require.Error(t, err)
		assert.Contains(t, err.Error(), "unexpected status code")
	})

	t.Run("processImageContext returns empty without contexts", func(t *testing.T) {
		server := httptest.NewServer(http.NotFoundHandler())
		defer server.Close()
		client := createTestClientWithServer(t, server)
		img := Base("python:3.12")
		ctxHashes, err := client.Snapshot.processImageContext(context.Background(), img)
		require.NoError(t, err)
		assert.Empty(t, ctxHashes)
	})

	t.Run("map snapshot preserves optional fields", func(t *testing.T) {
		now := time.Now().UTC()
		sizeVal := float32(42.5)
		size := *apiclient.NewNullableFloat32(&sizeVal)
		errorReason := *apiclient.NewNullableString(nil)
		lastUsedAt := *apiclient.NewNullableTime(nil)
		sourceSandboxIDVal := "sandbox-9"
		sourceSandboxID := *apiclient.NewNullableString(&sourceSandboxIDVal)
		apiSnapshot := apiclient.NewSnapshotDto("snap-3", false, "mapped", apiclient.SNAPSHOTSTATE_ACTIVE, size, []string{"python"}, 1, 0, 1024, 10, errorReason, now, now, lastUsedAt, sourceSandboxID)
		apiSnapshot.SetOrganizationId("org-9")
		apiSnapshot.SetImageName("python:3.12")
		mapped := mapSnapshotFromAPI(apiSnapshot)
		require.NotNil(t, mapped.Size)
		assert.Equal(t, 42.5, *mapped.Size)
		assert.Equal(t, "org-9", mapped.OrganizationID)
		require.NotNil(t, mapped.SourceSandboxID)
		assert.Equal(t, "sandbox-9", *mapped.SourceSandboxID)
	})
}

// httpCall is one recorded HTTP request against the fake API server.
type httpCall struct {
	Method string
	Path   string
}

// recordingHandler builds an http.Handler that records every incoming request
// into calls (guarded by mu) and dispatches to responders keyed by
// "METHOD PATH". A responder whose key exactly matches is preferred; otherwise
// the handler falls back to 404 so unregistered routes surface as ErrNotFound.
func recordingHandler(t *testing.T, mu *sync.Mutex, calls *[]httpCall, responders map[string]http.HandlerFunc) http.Handler {
	t.Helper()
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		*calls = append(*calls, httpCall{Method: r.Method, Path: r.URL.Path})
		mu.Unlock()
		key := r.Method + " " + r.URL.Path
		if h, ok := responders[key]; ok {
			h(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": "not found"})
	})
}

// snapshotUUID is a plain, canonical v4 UUID used as a real snapshot ID in
// tests. UUID-shaped NAMES use a different UUID so the two branches can be
// distinguished by the server-side fake.
const (
	snapshotUUID     = "11111111-1111-4111-8111-111111111111"
	snapshotNameUUID = "22222222-2222-4222-8222-222222222222"
)

func TestSnapshotDeleteByNameOrIDNonUUIDName(t *testing.T) {
	var (
		mu    sync.Mutex
		calls []httpCall
	)
	responders := map[string]http.HandlerFunc{
		"GET /snapshots/my-python-env": func(w http.ResponseWriter, r *http.Request) {
			writeJSONResponse(t, w, http.StatusOK, testSnapshotPayload(snapshotUUID, "my-python-env", apiclient.SNAPSHOTSTATE_ACTIVE))
		},
		"DELETE /snapshots/" + snapshotUUID: func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusNoContent)
		},
	}
	server := httptest.NewServer(recordingHandler(t, &mu, &calls, responders))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	require.NoError(t, client.Snapshot.DeleteByNameOrID(context.Background(), "my-python-env"))

	require.Equal(t, []httpCall{
		{Method: http.MethodGet, Path: "/snapshots/my-python-env"},
		{Method: http.MethodDelete, Path: "/snapshots/" + snapshotUUID},
	}, calls)
}

func TestSnapshotDeleteByNameOrIDPlainUUIDSingleCall(t *testing.T) {
	var (
		mu    sync.Mutex
		calls []httpCall
	)
	responders := map[string]http.HandlerFunc{
		"DELETE /snapshots/" + snapshotUUID: func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusNoContent)
		},
	}
	server := httptest.NewServer(recordingHandler(t, &mu, &calls, responders))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	require.NoError(t, client.Snapshot.DeleteByNameOrID(context.Background(), snapshotUUID))

	require.Equal(t, []httpCall{
		{Method: http.MethodDelete, Path: "/snapshots/" + snapshotUUID},
	}, calls, "plain UUID must skip the GET fallback and issue exactly one DELETE")
}

func TestSnapshotDeleteByNameOrIDUUIDFormattedNameFallback(t *testing.T) {
	var (
		mu    sync.Mutex
		calls []httpCall
	)
	// The reference IS a UUID-shaped string but it is another snapshot's NAME.
	// snapshotNameUUID does not exist as an ID, so the optimistic DELETE 404s;
	// the fallback GET resolves the name to snapshotUUID; the final DELETE
	// targets snapshotUUID.
	responders := map[string]http.HandlerFunc{
		"DELETE /snapshots/" + snapshotNameUUID: func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusNotFound)
			_ = json.NewEncoder(w).Encode(map[string]string{"message": "snapshot not found"})
		},
		"GET /snapshots/" + snapshotNameUUID: func(w http.ResponseWriter, r *http.Request) {
			writeJSONResponse(t, w, http.StatusOK, testSnapshotPayload(snapshotUUID, snapshotNameUUID, apiclient.SNAPSHOTSTATE_ACTIVE))
		},
		"DELETE /snapshots/" + snapshotUUID: func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusNoContent)
		},
	}
	server := httptest.NewServer(recordingHandler(t, &mu, &calls, responders))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	require.NoError(t, client.Snapshot.DeleteByNameOrID(context.Background(), snapshotNameUUID))

	require.Equal(t, []httpCall{
		{Method: http.MethodDelete, Path: "/snapshots/" + snapshotNameUUID},
		{Method: http.MethodGet, Path: "/snapshots/" + snapshotNameUUID},
		{Method: http.MethodDelete, Path: "/snapshots/" + snapshotUUID},
	}, calls)
}

func TestSnapshotDeleteByNameOrIDUUIDNon404NoFallback(t *testing.T) {
	var (
		mu    sync.Mutex
		calls []httpCall
	)
	responders := map[string]http.HandlerFunc{
		"DELETE /snapshots/" + snapshotUUID: func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusForbidden)
			_ = json.NewEncoder(w).Encode(map[string]string{"message": "forbidden"})
		},
	}
	server := httptest.NewServer(recordingHandler(t, &mu, &calls, responders))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	err := client.Snapshot.DeleteByNameOrID(context.Background(), snapshotUUID)
	require.Error(t, err)
	assert.True(t, stderrors.Is(err, sdkerrors.ErrForbidden), "expected 403 to satisfy ErrForbidden, got %v", err)
	require.Equal(t, []httpCall{
		{Method: http.MethodDelete, Path: "/snapshots/" + snapshotUUID},
	}, calls, "non-404 errors must propagate immediately without a GET fallback")
}

func TestSnapshotActivateByName(t *testing.T) {
	var (
		mu    sync.Mutex
		calls []httpCall
	)
	responders := map[string]http.HandlerFunc{
		"GET /snapshots/my-python-env": func(w http.ResponseWriter, r *http.Request) {
			writeJSONResponse(t, w, http.StatusOK, testSnapshotPayload(snapshotUUID, "my-python-env", apiclient.SNAPSHOTSTATE_INACTIVE))
		},
		"POST /snapshots/" + snapshotUUID + "/activate": func(w http.ResponseWriter, r *http.Request) {
			writeJSONResponse(t, w, http.StatusOK, testSnapshotPayload(snapshotUUID, "my-python-env", apiclient.SNAPSHOTSTATE_ACTIVE))
		},
	}
	server := httptest.NewServer(recordingHandler(t, &mu, &calls, responders))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	snap, err := client.Snapshot.Activate(context.Background(), "my-python-env")
	require.NoError(t, err)
	require.NotNil(t, snap)
	assert.Equal(t, snapshotUUID, snap.ID)
	assert.Equal(t, "my-python-env", snap.Name)
	assert.Equal(t, string(apiclient.SNAPSHOTSTATE_ACTIVE), snap.State)

	require.Equal(t, []httpCall{
		{Method: http.MethodGet, Path: "/snapshots/my-python-env"},
		{Method: http.MethodPost, Path: "/snapshots/" + snapshotUUID + "/activate"},
	}, calls)
}

func TestSnapshotDeleteByNameOrIDNonexistentNameErrNotFound(t *testing.T) {
	var (
		mu    sync.Mutex
		calls []httpCall
	)
	// No responders registered → every route falls through to 404 in the
	// recording handler, which is exactly what the server does when a name
	// does not resolve.
	server := httptest.NewServer(recordingHandler(t, &mu, &calls, nil))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	err := client.Snapshot.DeleteByNameOrID(context.Background(), "ghost-snapshot")
	require.Error(t, err)
	assert.True(t, stderrors.Is(err, sdkerrors.ErrNotFound), "expected error to satisfy sdkerrors.ErrNotFound, got %v", err)
	require.Equal(t, []httpCall{
		{Method: http.MethodGet, Path: "/snapshots/ghost-snapshot"},
	}, calls)
}
