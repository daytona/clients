// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.model;

/** CPU cores, memory GiB and disk GiB for one appended node. */
public final class NodeResources {
    private final int cpu;
    private final int memory;
    private final int disk;

    public NodeResources(int cpu, int memory, int disk) {
        if (cpu <= 0 || memory <= 0 || disk <= 0) {
            throw new io.daytona.sdk.exception.DaytonaException("Node CPU, memory and disk must be positive");
        }
        this.cpu = cpu;
        this.memory = memory;
        this.disk = disk;
    }

    public int getCpu() { return cpu; }
    public int getMemory() { return memory; }
    public int getDisk() { return disk; }
}
