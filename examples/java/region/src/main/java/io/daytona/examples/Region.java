// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.examples;

import io.daytona.sdk.Daytona;
import io.daytona.sdk.DaytonaConfig;
import io.daytona.sdk.Sandbox;
import io.daytona.sdk.model.CreateSandboxFromSnapshotParams;

public class Region {
    public static void main(String[] args) {
        DaytonaConfig config = new DaytonaConfig.Builder()
                .apiKey(System.getenv("DAYTONA_API_KEY"))
                .apiUrl(System.getenv("DAYTONA_API_URL") != null
                        ? System.getenv("DAYTONA_API_URL")
                        : "https://app.daytona.io/api")
                .build();

        try (Daytona daytona = new Daytona(config)) {
            System.out.println("Creating sandbox with target: us");
            CreateSandboxFromSnapshotParams params = new CreateSandboxFromSnapshotParams();
            params.setTarget("us");
            Sandbox sandbox = daytona.create(params);
            try {
                System.out.println("Sandbox created: " + sandbox.getId());
                System.out.println("target: " + sandbox.getTarget());
            } finally {
                System.out.println("Deleting sandbox");
                sandbox.delete();
            }
        }
    }
}
