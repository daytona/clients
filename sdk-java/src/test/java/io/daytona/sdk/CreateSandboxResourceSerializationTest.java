// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import static org.assertj.core.api.Assertions.assertThat;

import io.daytona.api.client.model.CreateSandbox;
import java.util.Arrays;
import org.junit.jupiter.api.Test;

class CreateSandboxResourceSerializationTest {
    @Test
    void ordinaryRequestsKeepTheirScalarApiAndWireFormat() throws Exception {
        CreateSandbox request = new CreateSandbox().cpu(16).memory(128).disk(256);
        String json = request.toJson();
        for (String key : Arrays.asList("cpu", "memory", "disk")) {
            assertThat(json).doesNotContain("\"" + key + "PerNode\"");
        }
        assertThat(json).contains("\"cpu\":16", "\"memory\":128", "\"disk\":256");
        Integer cpu = CreateSandbox.fromJson(request.toJson()).getCpu();
        assertThat(cpu).isEqualTo(16);
    }

    @Test
    void perNodeRequestsRoundTripIndependentlyOfScalarFields() throws Exception {
        CreateSandbox request = new CreateSandbox().cpuPerNode(Arrays.asList(16, 32)).memory(128)
                .diskPerNode(Arrays.asList(256, 512));
        CreateSandbox decoded = CreateSandbox.fromJson(request.toJson());
        assertThat(decoded.getCpu()).isNull();
        assertThat(decoded.getCpuPerNode()).containsExactly(16, 32);
        assertThat(decoded.getMemory()).isEqualTo(128);
        assertThat(decoded.getDiskPerNode()).containsExactly(256, 512);
    }
}
