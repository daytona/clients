// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	apiclient "github.com/daytona/clients/api-client-go"
	"github.com/daytona/clients/sdk-go/pkg/types"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestSecretServiceCreation(t *testing.T) {
	t.Setenv("DAYTONA_API_KEY", "test-api-key")
	t.Setenv("DAYTONA_API_URL", "")
	t.Setenv("DAYTONA_JWT_TOKEN", "")
	t.Setenv("DAYTONA_ORGANIZATION_ID", "")

	client, err := NewClient()
	require.NoError(t, err)

	ss := NewSecretService(client)
	require.NotNil(t, ss)
	require.NotNil(t, client.Secret)
}

func TestSecretGetError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": "not found"})
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)

	ctx := context.Background()
	_, err := client.Secret.Get(ctx, "nonexistent")
	require.Error(t, err)
}

func TestSecretDeleteError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": "not found"})
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)

	ctx := context.Background()
	err := client.Secret.Delete(ctx, "nonexistent")
	require.Error(t, err)
}

func TestSecretDtoToSecret(t *testing.T) {
	dto := apiclient.NewSecretWithDefaults()
	dto.SetId("secret-1")
	dto.SetName("anthropic-prod")
	dto.SetDescription("prod key")
	dto.SetPlaceholder("{{secret:secret-1}}")
	dto.SetHosts([]string{"api.anthropic.com", "*.anthropic.com"})

	secret := secretDtoToSecret(dto)
	assert.Equal(t, "secret-1", secret.ID)
	assert.Equal(t, "anthropic-prod", secret.Name)
	require.NotNil(t, secret.Description)
	assert.Equal(t, "prod key", *secret.Description)
	assert.Equal(t, "{{secret:secret-1}}", secret.Placeholder)
	assert.Equal(t, []string{"api.anthropic.com", "*.anthropic.com"}, secret.Hosts)
}

func TestSecretSuccessOperations(t *testing.T) {
	t.Run("create list get update and delete succeed", func(t *testing.T) {
		var lastCreateBody apiclient.CreateSecret
		var lastUpdateBody apiclient.UpdateSecret

		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			switch r.Method {
			case http.MethodGet:
				if strings.HasSuffix(r.URL.Path, "/secret/paginated") {
					writeJSONResponse(t, w, http.StatusOK, map[string]any{
						"items":      []any{testSecretPayload("secret-1", "anthropic-prod")},
						"total":      1,
						"nextCursor": nil,
					})
					return
				}
				writeJSONResponse(t, w, http.StatusOK, testSecretPayload("secret-1", "anthropic-prod"))
			case http.MethodPost:
				body, _ := io.ReadAll(r.Body)
				require.NoError(t, json.Unmarshal(body, &lastCreateBody))
				writeJSONResponse(t, w, http.StatusCreated, testSecretPayload("secret-1", "anthropic-prod"))
			case http.MethodPatch:
				body, _ := io.ReadAll(r.Body)
				require.NoError(t, json.Unmarshal(body, &lastUpdateBody))
				writeJSONResponse(t, w, http.StatusOK, testSecretPayload("secret-1", "anthropic-prod"))
			case http.MethodDelete:
				w.WriteHeader(http.StatusNoContent)
			}
		}))
		defer server.Close()

		client := createTestClientWithServer(t, server)
		ctx := context.Background()

		page, err := client.Secret.List(ctx, nil)
		require.NoError(t, err)
		require.Len(t, page.Items, 1)
		assert.Equal(t, "anthropic-prod", page.Items[0].Name)

		secret, err := client.Secret.Get(ctx, "secret-1")
		require.NoError(t, err)
		assert.Equal(t, "secret-1", secret.ID)
		assert.Equal(t, "{{secret:secret-1}}", secret.Placeholder)

		desc := "prod key"
		created, err := client.Secret.Create(ctx, &types.CreateSecretParams{
			Name:        "anthropic-prod",
			Value:       "sk-ant-secret",
			Description: &desc,
			Hosts:       []string{"api.anthropic.com"},
		})
		require.NoError(t, err)
		assert.Equal(t, "anthropic-prod", created.Name)
		assert.Equal(t, "anthropic-prod", lastCreateBody.GetName())
		assert.Equal(t, "sk-ant-secret", lastCreateBody.GetValue())
		assert.Equal(t, "prod key", lastCreateBody.GetDescription())
		assert.Equal(t, []string{"api.anthropic.com"}, lastCreateBody.GetHosts())

		newValue := "sk-ant-rotated"
		updated, err := client.Secret.Update(ctx, "secret-1", &types.UpdateSecretParams{
			Value: &newValue,
			Hosts: []string{"api.anthropic.com", "*.anthropic.com"},
		})
		require.NoError(t, err)
		assert.Equal(t, "secret-1", updated.ID)
		assert.Equal(t, "sk-ant-rotated", lastUpdateBody.GetValue())
		assert.Equal(t, []string{"api.anthropic.com", "*.anthropic.com"}, lastUpdateBody.GetHosts())

		require.NoError(t, client.Secret.Delete(ctx, "secret-1"))
	})
}

func TestSecretList(t *testing.T) {
	t.Run("query params are serialized correctly", func(t *testing.T) {
		var lastQuery url.Values

		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			require.Equal(t, http.MethodGet, r.Method)
			require.True(t, strings.HasSuffix(r.URL.Path, "/secret/paginated"))
			lastQuery = r.URL.Query()
			writeJSONResponse(t, w, http.StatusOK, map[string]any{
				"items":      []any{},
				"total":      0,
				"nextCursor": nil,
			})
		}))
		defer server.Close()

		client := createTestClientWithServer(t, server)

		_, err := client.Secret.List(context.Background(), &types.ListSecretsQuery{
			Cursor: strPtr("cursor-abc"),
			Limit:  intPtr(50),
			Name:   strPtr("anthropic"),
			Sort:   strPtr("name"),
			Order:  strPtr("asc"),
		})
		require.NoError(t, err)

		assert.Equal(t, "cursor-abc", lastQuery.Get("cursor"))
		assert.Equal(t, "50", lastQuery.Get("limit"))
		assert.Equal(t, "anthropic", lastQuery.Get("name"))
		assert.Equal(t, "name", lastQuery.Get("sort"))
		assert.Equal(t, "asc", lastQuery.Get("order"))
	})

	t.Run("response is mapped with nextCursor set", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			writeJSONResponse(t, w, http.StatusOK, map[string]any{
				"items": []any{
					testSecretPayload("secret-1", "anthropic-prod"),
					testSecretPayload("secret-2", "anthropic-dev"),
				},
				"total":      42,
				"nextCursor": "cursor-next",
			})
		}))
		defer server.Close()

		client := createTestClientWithServer(t, server)

		page, err := client.Secret.List(context.Background(), nil)
		require.NoError(t, err)
		require.Len(t, page.Items, 2)
		assert.Equal(t, "secret-1", page.Items[0].ID)
		assert.Equal(t, "anthropic-prod", page.Items[0].Name)
		assert.Equal(t, "{{secret:secret-1}}", page.Items[0].Placeholder)
		assert.Equal(t, "secret-2", page.Items[1].ID)
		assert.Equal(t, 42, page.Total)
		require.NotNil(t, page.NextCursor)
		assert.Equal(t, "cursor-next", *page.NextCursor)
	})

	t.Run("nextCursor is nil on the last page", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			writeJSONResponse(t, w, http.StatusOK, map[string]any{
				"items":      []any{testSecretPayload("secret-1", "anthropic-prod")},
				"total":      1,
				"nextCursor": nil,
			})
		}))
		defer server.Close()

		client := createTestClientWithServer(t, server)

		page, err := client.Secret.List(context.Background(), nil)
		require.NoError(t, err)
		require.Len(t, page.Items, 1)
		assert.Equal(t, 1, page.Total)
		assert.Nil(t, page.NextCursor)
	})

	t.Run("error responses are converted", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			writeJSONResponse(t, w, http.StatusInternalServerError, map[string]string{"message": "internal error"})
		}))
		defer server.Close()

		client := createTestClientWithServer(t, server)

		_, err := client.Secret.List(context.Background(), nil)
		require.Error(t, err)
	})
}

// TestCreateSandboxSecretsSerialization verifies that the SDK secrets map
// (env var name -> secret name) is serialized to the API's array of
// single-key maps.
func TestCreateSandboxSecretsSerialization(t *testing.T) {
	var createBody apiclient.CreateSandbox

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/sandbox") {
			body, _ := io.ReadAll(r.Body)
			require.NoError(t, json.Unmarshal(body, &createBody))
			writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb-1", "sandbox", apiclient.SANDBOXSTATE_STARTED))
			return
		}
		writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb-1", "sandbox", apiclient.SANDBOXSTATE_STARTED))
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)

	_, err := client.Create(context.Background(), types.SnapshotParams{
		Snapshot: "my-snapshot",
		SandboxBaseParams: types.SandboxBaseParams{
			Language: types.CodeLanguagePython,
			Secrets: map[string]string{
				"ANTHROPIC_API_KEY": "anthropic-prod",
			},
		},
	})
	require.NoError(t, err)

	secrets := createBody.GetSecrets()
	require.Len(t, secrets, 1)
	assert.Equal(t, map[string]string{"ANTHROPIC_API_KEY": "anthropic-prod"}, secrets[0])
}
