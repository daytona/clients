// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.examples;

import io.daytona.sdk.Daytona;
import io.daytona.sdk.DaytonaConfig;
import io.daytona.sdk.Sandbox;

public class Region {
    public static void main(String[] args) {
        DaytonaConfig config = new DaytonaConfig.Builder()
                .apiKey(System.getenv("DAYTONA_API_KEY"))
                .apiUrl(System.getenv("DAYTONA_API_URL") != null
                        ? System.getenv("DAYTONA_API_URL")
                        : Daytona.V1_API_URL)
                .build();

        try (Daytona daytona = new Daytona(config)) {
            var v1 = daytona.v1();
            System.out.println("Creating sandbox with target: us");
            var params = new io.daytona.sdk.model.CreateSandboxFromSnapshotParams();
            params.setTarget("us");
            Sandbox sandbox = v1.create(params);
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
