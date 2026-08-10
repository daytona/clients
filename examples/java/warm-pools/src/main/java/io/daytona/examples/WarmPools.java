// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.examples;

import io.daytona.sdk.Daytona;
import io.daytona.sdk.model.WarmPool;

public class WarmPools {
    public static void main(String[] args) {
        try (Daytona daytona = new Daytona()) {
            // Create a warm pool that keeps ready-to-use sandboxes for an existing snapshot.
            // The target (third argument) is optional; null means the organization default region.
            WarmPool pool = daytona.warmPool().create("my-snapshot", 3, null);
            System.out.println("Created warm pool " + pool.getId()
                    + " for snapshot '" + pool.getSnapshot() + "' in " + pool.getTarget());

            try {
                // List warm pools. currentSize vs pool is the status check: currentSize is the
                // number of ready sandboxes; errorReason is set when the pool cannot be filled.
                for (WarmPool p : daytona.warmPool().list()) {
                    String status = p.getErrorReason() != null ? " (error: " + p.getErrorReason() + ")" : "";
                    System.out.println(p.getSnapshot() + " (" + p.getTarget() + "): "
                            + p.getCurrentSize() + "/" + p.getPool() + " ready" + status);
                }

                // Grow the pool. Setting the size to 0 drains it without deleting the pool.
                WarmPool updated = daytona.warmPool().update(pool.getId(), 5);
                System.out.println("Updated desired size to " + updated.getPool());
            } finally {
                // Cleanup runs even if a call above fails.
                daytona.warmPool().delete(pool.getId());
                System.out.println("Deleted warm pool");
            }
        }
    }
}
