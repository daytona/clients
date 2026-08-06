package main

import (
	"context"
	"log"

	"github.com/daytona/clients/sdk-go/pkg/daytona"
	"github.com/daytona/clients/sdk-go/pkg/types"
)

func main() {
	// Create a new Daytona client using environment variables.
	// Set DAYTONA_API_KEY before running.
	client, err := daytona.NewClient()
	if err != nil {
		log.Fatalf("Failed to create client: %v", err)
	}

	ctx := context.Background()

	// Create a warm pool that keeps ready-to-use sandboxes for an existing snapshot.
	// Target is optional and defaults to the organization default region.
	pool, err := client.WarmPool.Create(ctx, &types.CreateWarmPoolParams{
		Snapshot: "my-snapshot",
		Pool:     3,
	})
	if err != nil {
		log.Fatalf("Failed to create warm pool: %v", err)
	}
	log.Printf("✓ Created warm pool %s for snapshot %q in %s\n", pool.ID, pool.Snapshot, pool.Target)

	// List warm pools. CurrentSize vs Pool is the status check: CurrentSize is the
	// number of ready sandboxes; ErrorReason is set when the pool cannot be filled.
	pools, err := client.WarmPool.List(ctx)
	if err != nil {
		log.Fatalf("Failed to list warm pools: %v", err)
	}
	for _, p := range pools {
		status := ""
		if p.ErrorReason != nil {
			status = " (error: " + *p.ErrorReason + ")"
		}
		log.Printf("%s (%s): %d/%d ready%s\n", p.Snapshot, p.Target, p.CurrentSize, p.Pool, status)
	}

	// Grow the pool. Setting the size to 0 drains it without deleting the pool.
	updated, err := client.WarmPool.Update(ctx, pool.ID, 5)
	if err != nil {
		log.Fatalf("Failed to update warm pool: %v", err)
	}
	log.Printf("✓ Updated desired size to %d\n", updated.Pool)

	// Cleanup
	if err := client.WarmPool.Delete(ctx, pool.ID); err != nil {
		log.Fatalf("Failed to delete warm pool: %v", err)
	}
	log.Println("✓ Deleted warm pool")
}
