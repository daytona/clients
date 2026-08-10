// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"time"

	apiclient "github.com/daytona/clients/api-client-go"
	"github.com/daytona/clients/sdk-go/pkg/errors"
	"github.com/daytona/clients/sdk-go/pkg/types"
)

// WarmPoolService provides warm pool management operations.
//
// WarmPoolService enables listing, creating, updating, and deleting warm
// pools of ready-to-use sandboxes for a snapshot. A pool's CurrentSize versus
// Pool is its status: CurrentSize is the number of ready warm sandboxes, Pool
// is the desired number, and ErrorReason is set when the pool cannot be
// filled. Access through [Client.WarmPool].
//
// Example:
//
//	// Create a new warm pool
//	pool, err := client.WarmPool.Create(ctx, &types.CreateWarmPoolParams{
//	    Snapshot: "my-snapshot",
//	    Pool:     5,
//	})
//	if err != nil {
//	    return err
//	}
//
//	// List all warm pools
//	pools, err := client.WarmPool.List(ctx)
type WarmPoolService struct {
	client *Client
	otel   *otelState
}

// NewWarmPoolService creates a new WarmPoolService.
//
// This is typically called internally by the SDK when creating a [Client].
// Users should access WarmPoolService through [Client.WarmPool] rather than
// creating it directly.
func NewWarmPoolService(client *Client) *WarmPoolService {
	return &WarmPoolService{
		client: client,
		otel:   client.Otel,
	}
}

// List returns all warm pools in the organization.
//
// Example:
//
//	pools, err := client.WarmPool.List(ctx)
//	if err != nil {
//	    return err
//	}
//	for _, pool := range pools {
//	    fmt.Printf("%s: %d/%d ready\n", pool.Snapshot, pool.CurrentSize, pool.Pool)
//	}
//
// Returns a slice of [types.WarmPool] or an error if the request fails.
func (w *WarmPoolService) List(ctx context.Context) ([]*types.WarmPool, error) {
	return withInstrumentation(ctx, w.otel, "WarmPool", "List", func(ctx context.Context) ([]*types.WarmPool, error) {
		authCtx := w.client.getAuthContext(ctx)
		warmPoolDtos, httpResp, err := w.client.apiClient.WarmPoolsAPI.ListWarmPools(authCtx).Execute()
		if err != nil {
			return nil, errors.ConvertAPIError(err, httpResp)
		}

		warmPools := make([]*types.WarmPool, len(warmPoolDtos))
		for i := range warmPoolDtos {
			warmPools[i] = warmPoolDtoToWarmPool(&warmPoolDtos[i])
		}

		return warmPools, nil
	})
}

// Create creates a new warm pool.
//
// A pool for the same snapshot and region may exist only once; a duplicate
// returns a conflict error.
//
// Parameters:
//   - params: Warm pool creation parameters including snapshot, desired pool
//     size, and optional target region
//
// Example:
//
//	pool, err := client.WarmPool.Create(ctx, &types.CreateWarmPoolParams{
//	    Snapshot: "my-snapshot",
//	    Pool:     5,
//	})
//	if err != nil {
//	    return err
//	}
//
// Returns the created [types.WarmPool] or an error.
func (w *WarmPoolService) Create(ctx context.Context, params *types.CreateWarmPoolParams) (*types.WarmPool, error) {
	return withInstrumentation(ctx, w.otel, "WarmPool", "Create", func(ctx context.Context) (*types.WarmPool, error) {
		authCtx := w.client.getAuthContext(ctx)

		req := apiclient.NewCreateWarmPool(params.Snapshot, float32(params.Pool))
		if params.Target != nil {
			req.SetTarget(*params.Target)
		}

		warmPoolDto, httpResp, err := w.client.apiClient.WarmPoolsAPI.CreateWarmPool(authCtx).CreateWarmPool(*req).Execute()
		if err != nil {
			return nil, errors.ConvertAPIError(err, httpResp)
		}

		return warmPoolDtoToWarmPool(warmPoolDto), nil
	})
}

// Update sets the desired size of a warm pool.
//
// Parameters:
//   - warmPoolID: The warm pool ID
//   - pool: New desired number of warm sandboxes (0 drains the pool)
//
// Example:
//
//	pool, err := client.WarmPool.Update(ctx, warmPoolID, 10)
//	if err != nil {
//	    return err
//	}
//
// Returns the updated [types.WarmPool] or an error if the ID is unknown (404).
func (w *WarmPoolService) Update(ctx context.Context, warmPoolID string, pool int) (*types.WarmPool, error) {
	return withInstrumentation(ctx, w.otel, "WarmPool", "Update", func(ctx context.Context) (*types.WarmPool, error) {
		authCtx := w.client.getAuthContext(ctx)

		req := apiclient.NewUpdateWarmPool(float32(pool))
		warmPoolDto, httpResp, err := w.client.apiClient.WarmPoolsAPI.UpdateWarmPool(authCtx, warmPoolID).UpdateWarmPool(*req).Execute()
		if err != nil {
			return nil, errors.ConvertAPIError(err, httpResp)
		}

		return warmPoolDtoToWarmPool(warmPoolDto), nil
	})
}

// Delete permanently removes a warm pool.
//
// Parameters:
//   - warmPoolID: The warm pool ID
//
// Example:
//
//	err := client.WarmPool.Delete(ctx, warmPoolID)
//	if err != nil {
//	    return err
//	}
//
// Returns an error if the ID is unknown (404) or deletion fails.
func (w *WarmPoolService) Delete(ctx context.Context, warmPoolID string) error {
	return withInstrumentationVoid(ctx, w.otel, "WarmPool", "Delete", func(ctx context.Context) error {
		authCtx := w.client.getAuthContext(ctx)
		httpResp, err := w.client.apiClient.WarmPoolsAPI.DeleteWarmPool(authCtx, warmPoolID).Execute()
		if err != nil {
			return errors.ConvertAPIError(err, httpResp)
		}

		return nil
	})
}

// warmPoolDtoToWarmPool converts api-client WarmPool to SDK types.WarmPool
func warmPoolDtoToWarmPool(dto *apiclient.WarmPool) *types.WarmPool {
	createdAt, _ := time.Parse(time.RFC3339, dto.GetCreatedAt())
	updatedAt, _ := time.Parse(time.RFC3339, dto.GetUpdatedAt())

	warmPool := &types.WarmPool{
		ID:             dto.GetId(),
		OrganizationID: dto.GetOrganizationId(),
		Snapshot:       dto.GetSnapshot(),
		Target:         dto.GetTarget(),
		Pool:           int(dto.GetPool()),
		CurrentSize:    int(dto.GetCurrentSize()),
		CPU:            int(dto.GetCpu()),
		Mem:            int(dto.GetMem()),
		Disk:           int(dto.GetDisk()),
		OsUser:         dto.GetOsUser(),
		Env:            dto.GetEnv(),
		CreatedAt:      createdAt,
		UpdatedAt:      updatedAt,
	}

	// Handle nullable ErrorReason
	if dto.ErrorReason.IsSet() {
		warmPool.ErrorReason = dto.ErrorReason.Get()
	}

	return warmPool
}
