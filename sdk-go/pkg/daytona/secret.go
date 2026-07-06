// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"

	apiclient "github.com/daytona/clients/api-client-go"
	"github.com/daytona/clients/sdk-go/pkg/errors"
	"github.com/daytona/clients/sdk-go/pkg/types"
)

// SecretService provides organization-scoped secret management operations.
//
// SecretService enables creating, listing, retrieving, updating, and deleting
// secrets. A secret stores a write-only plaintext value that is never returned
// by the API. Secrets can be referenced when creating a sandbox (see the
// Secrets field on [types.SandboxBaseParams]); the env var injected into the
// sandbox holds the secret's opaque placeholder, which is resolved to the real
// value only for the secret's allowed hosts. Access through [Client.Secret].
//
// Example:
//
//	// Create a new secret
//	secret, err := client.Secret.Create(ctx, &types.CreateSecretParams{
//	    Name:  "anthropic-prod",
//	    Value: "sk-ant-...",
//	    Hosts: []string{"api.anthropic.com"},
//	})
//	if err != nil {
//	    return err
//	}
//
//	// List secrets page by page
//	page, err := client.Secret.List(ctx, nil)
type SecretService struct {
	client *Client
	otel   *otelState
}

// NewSecretService creates a new SecretService.
//
// This is typically called internally by the SDK when creating a [Client].
// Users should access SecretService through [Client.Secret] rather than
// creating it directly.
func NewSecretService(client *Client) *SecretService {
	return &SecretService{
		client: client,
		otel:   client.Otel,
	}
}

// List returns one page of the organization's secrets.
//
// The plaintext value is never returned; each secret carries only its opaque
// placeholder. Pass the NextCursor of a response as the Cursor of the next
// query to fetch the following page; a nil NextCursor means there are no
// further pages.
//
// Parameters:
//   - query: Optional filtering, sorting, and pagination parameters. May be
//     nil, in which case the first page is returned with server defaults
//     (100 results sorted by creation time, newest first).
//
// Example:
//
//	query := &types.ListSecretsQuery{}
//	for {
//	    page, err := client.Secret.List(ctx, query)
//	    if err != nil {
//	        return err
//	    }
//	    for _, secret := range page.Items {
//	        fmt.Printf("Secret %s -> %s\n", secret.Name, secret.Placeholder)
//	    }
//	    if page.NextCursor == nil {
//	        break
//	    }
//	    query.Cursor = page.NextCursor
//	}
//
// Returns a [types.ListSecretsResponse] holding the page's secrets, the total
// number of secrets matching the filters, and the next-page cursor, or an
// error if the request fails.
func (s *SecretService) List(ctx context.Context, query *types.ListSecretsQuery) (*types.ListSecretsResponse, error) {
	return withInstrumentation(ctx, s.otel, "Secret", "List", func(ctx context.Context) (*types.ListSecretsResponse, error) {
		authCtx := s.client.getAuthContext(ctx)

		req := s.client.apiClient.SecretAPI.ListSecretsPaginated(authCtx)
		if query != nil {
			if query.Cursor != nil {
				req = req.Cursor(*query.Cursor)
			}
			if query.Limit != nil {
				req = req.Limit(float32(*query.Limit))
			}
			if query.Name != nil {
				req = req.Name(*query.Name)
			}
			if query.Sort != nil {
				req = req.Sort(*query.Sort)
			}
			if query.Order != nil {
				req = req.Order(*query.Order)
			}
		}

		resp, httpResp, err := req.Execute()
		if err != nil {
			return nil, errors.ConvertAPIError(err, httpResp)
		}

		items := make([]*types.Secret, len(resp.Items))
		for i := range resp.Items {
			items[i] = secretDtoToSecret(&resp.Items[i])
		}

		result := &types.ListSecretsResponse{
			Items: items,
			Total: int(resp.GetTotal()),
		}
		if nextCursor, ok := resp.GetNextCursorOk(); ok && nextCursor != nil {
			result.NextCursor = nextCursor
		}

		return result, nil
	})
}

// Get retrieves a secret by its ID.
//
// Parameters:
//   - secretID: The secret ID
//
// Example:
//
//	secret, err := client.Secret.Get(ctx, secretID)
//	if err != nil {
//	    var notFound *errors.DaytonaNotFoundError
//	    if errors.As(err, &notFound) {
//	        log.Println("Secret not found")
//	    }
//	    return err
//	}
//	fmt.Printf("Secret %s allows hosts: %v\n", secret.Name, secret.Hosts)
//
// Returns the [types.Secret] or an error if the ID is unknown (404).
func (s *SecretService) Get(ctx context.Context, secretID string) (*types.Secret, error) {
	return withInstrumentation(ctx, s.otel, "Secret", "Get", func(ctx context.Context) (*types.Secret, error) {
		authCtx := s.client.getAuthContext(ctx)
		secretDto, httpResp, err := s.client.apiClient.SecretAPI.GetSecret(authCtx, secretID).Execute()
		if err != nil {
			return nil, errors.ConvertAPIError(err, httpResp)
		}

		return secretDtoToSecret(secretDto), nil
	})
}

// Create creates a new organization secret.
//
// The plaintext value is write-only and is never returned. The name must match
// ^[a-zA-Z_][a-zA-Z0-9_-]*$ and be unique within the organization; a duplicate
// name returns a conflict error.
//
// Parameters:
//   - params: Secret creation parameters including name, value, optional
//     description, and allowed hosts
//
// Example:
//
//	secret, err := client.Secret.Create(ctx, &types.CreateSecretParams{
//	    Name:  "anthropic-prod",
//	    Value: "sk-ant-...",
//	    Hosts: []string{"api.anthropic.com"},
//	})
//	if err != nil {
//	    return err
//	}
//
// Returns the created [types.Secret] or an error.
func (s *SecretService) Create(ctx context.Context, params *types.CreateSecretParams) (*types.Secret, error) {
	return withInstrumentation(ctx, s.otel, "Secret", "Create", func(ctx context.Context) (*types.Secret, error) {
		authCtx := s.client.getAuthContext(ctx)

		req := apiclient.NewCreateSecret(params.Name, params.Value)
		if params.Description != nil {
			req.SetDescription(*params.Description)
		}
		if params.Hosts != nil {
			req.SetHosts(params.Hosts)
		}

		secretDto, httpResp, err := s.client.apiClient.SecretAPI.CreateSecret(authCtx).CreateSecret(*req).Execute()
		if err != nil {
			return nil, errors.ConvertAPIError(err, httpResp)
		}

		return secretDtoToSecret(secretDto), nil
	})
}

// Update modifies an existing secret identified by its ID.
//
// Only the non-nil fields of params are applied. The plaintext value is
// write-only and is never returned.
//
// Parameters:
//   - secretID: The secret ID
//   - params: Fields to update (value, description, allowed hosts)
//
// Example:
//
//	newValue := "sk-ant-rotated-..."
//	secret, err := client.Secret.Update(ctx, secretID, &types.UpdateSecretParams{
//	    Value: &newValue,
//	    Hosts: []string{"api.anthropic.com", "*.anthropic.com"},
//	})
//	if err != nil {
//	    return err
//	}
//
// Returns the updated [types.Secret] or an error if the ID is unknown (404).
func (s *SecretService) Update(ctx context.Context, secretID string, params *types.UpdateSecretParams) (*types.Secret, error) {
	return withInstrumentation(ctx, s.otel, "Secret", "Update", func(ctx context.Context) (*types.Secret, error) {
		authCtx := s.client.getAuthContext(ctx)

		req := apiclient.NewUpdateSecret()
		if params.Value != nil {
			req.SetValue(*params.Value)
		}
		if params.Description != nil {
			req.SetDescription(*params.Description)
		}
		if params.Hosts != nil {
			req.SetHosts(params.Hosts)
		}

		secretDto, httpResp, err := s.client.apiClient.SecretAPI.UpdateSecret(authCtx, secretID).UpdateSecret(*req).Execute()
		if err != nil {
			return nil, errors.ConvertAPIError(err, httpResp)
		}

		return secretDtoToSecret(secretDto), nil
	})
}

// Delete permanently removes a secret identified by its ID.
//
// This operation is irreversible.
//
// Parameters:
//   - secretID: The secret ID
//
// Example:
//
//	err := client.Secret.Delete(ctx, secretID)
//	if err != nil {
//	    return err
//	}
//
// Returns an error if the ID is unknown (404) or deletion fails.
func (s *SecretService) Delete(ctx context.Context, secretID string) error {
	return withInstrumentationVoid(ctx, s.otel, "Secret", "Delete", func(ctx context.Context) error {
		authCtx := s.client.getAuthContext(ctx)
		httpResp, err := s.client.apiClient.SecretAPI.DeleteSecret(authCtx, secretID).Execute()
		if err != nil {
			return errors.ConvertAPIError(err, httpResp)
		}

		return nil
	})
}

// secretDtoToSecret converts api-client Secret to SDK types.Secret
func secretDtoToSecret(dto *apiclient.Secret) *types.Secret {
	secret := &types.Secret{
		ID:          dto.GetId(),
		Name:        dto.GetName(),
		Placeholder: dto.GetPlaceholder(),
		Hosts:       dto.GetHosts(),
		CreatedAt:   dto.GetCreatedAt(),
		UpdatedAt:   dto.GetUpdatedAt(),
	}

	if description, ok := dto.GetDescriptionOk(); ok && description != nil {
		secret.Description = description
	}

	return secret
}
