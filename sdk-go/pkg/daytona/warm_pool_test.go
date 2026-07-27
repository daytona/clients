package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	apiclient "github.com/daytona/clients/api-client-go"
	"github.com/daytona/clients/sdk-go/pkg/types"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestWarmPoolServiceCreation(t *testing.T) {
	t.Setenv("DAYTONA_API_KEY", "test-api-key")
	t.Setenv("DAYTONA_API_URL", "")
	t.Setenv("DAYTONA_JWT_TOKEN", "")
	t.Setenv("DAYTONA_ORGANIZATION_ID", "")

	client, err := NewClient()
	require.NoError(t, err)

	ws := NewWarmPoolService(client)
	require.NotNil(t, ws)
}

func TestWarmPoolList(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode([]map[string]any{{
			"id":             "wp-1",
			"organizationId": "org-1",
			"snapshot":       "my-snapshot",
			"target":         "us",
			"pool":           5,
			"currentSize":    3,
			"cpu":            2,
			"mem":            4,
			"disk":           10,
			"osUser":         "daytona",
			"env":            map[string]string{},
			"createdAt":      "2025-01-01T00:00:00Z",
			"updatedAt":      "2025-01-02T00:00:00Z",
		}})
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)

	pools, err := client.WarmPool.List(context.Background())
	require.NoError(t, err)
	require.Len(t, pools, 1)
	assert.Equal(t, "wp-1", pools[0].ID)
	assert.Equal(t, "my-snapshot", pools[0].Snapshot)
	assert.Equal(t, 5, pools[0].Pool)
	assert.Equal(t, 3, pools[0].CurrentSize)
}

func TestWarmPoolListError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": "internal error"})
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)

	_, err := client.WarmPool.List(context.Background())
	require.Error(t, err)
}

func TestWarmPoolCreateError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": "warm pool already exists"})
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)

	_, err := client.WarmPool.Create(context.Background(), &types.CreateWarmPoolParams{
		Snapshot: "my-snapshot",
		Pool:     5,
	})
	require.Error(t, err)
}

func TestWarmPoolDeleteError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": "not found"})
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)

	err := client.WarmPool.Delete(context.Background(), "wp-1")
	require.Error(t, err)
}

func TestWarmPoolDtoToWarmPool(t *testing.T) {
	dto := apiclient.NewWarmPool(
		"wp-1", "org-1", "my-snapshot", "us", 5, 3, 2, 4, 10, "daytona",
		map[string]string{"FOO": "bar"}, "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z",
	)
	dto.SetErrorReason("quota exceeded")

	warmPool := warmPoolDtoToWarmPool(dto)

	assert.Equal(t, "wp-1", warmPool.ID)
	assert.Equal(t, "org-1", warmPool.OrganizationID)
	assert.Equal(t, "my-snapshot", warmPool.Snapshot)
	assert.Equal(t, "us", warmPool.Target)
	assert.Equal(t, 5, warmPool.Pool)
	assert.Equal(t, 3, warmPool.CurrentSize)
	assert.Equal(t, 2, warmPool.CPU)
	assert.Equal(t, 4, warmPool.Mem)
	assert.Equal(t, 10, warmPool.Disk)
	assert.Equal(t, "daytona", warmPool.OsUser)
	assert.Equal(t, map[string]string{"FOO": "bar"}, warmPool.Env)
	require.NotNil(t, warmPool.ErrorReason)
	assert.Equal(t, "quota exceeded", *warmPool.ErrorReason)
	assert.Equal(t, "2025-01-01T00:00:00Z", warmPool.CreatedAt.Format("2006-01-02T15:04:05Z"))
}
