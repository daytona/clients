// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import static org.assertj.core.api.Assertions.assertThat;

import io.daytona.api.client.model.CreateSandboxCpu;
import io.daytona.api.client.model.CreateSandboxDisk;
import io.daytona.api.client.model.CreateSandboxMemory;
import java.util.Arrays;
import org.junit.jupiter.api.Test;

class CreateSandboxResourceSerializationTest {
    @Test
    void scalarResourcesRemainJsonNumbers() {
        assertThat(new CreateSandboxCpu(16).toJson()).isEqualTo("16");
        assertThat(new CreateSandboxMemory(128).toJson()).isEqualTo("128");
        assertThat(new CreateSandboxDisk(256).toJson()).isEqualTo("256");
    }

    @Test
    void perNodeResourcesSerializeAsJsonArrays() {
        assertThat(new CreateSandboxCpu(Arrays.asList(16, 32)).toJson()).isEqualTo("[16,32]");
        assertThat(new CreateSandboxMemory(Arrays.asList(128, 256)).toJson()).isEqualTo("[128,256]");
        assertThat(new CreateSandboxDisk(Arrays.asList(256, 512)).toJson()).isEqualTo("[256,512]");
    }
}
