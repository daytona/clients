// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import io.daytona.api.client.model.GpuType;

import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
/**
 * Resource allocation values for a Sandbox.
 */
public class Resources {
    @JsonProperty("cpu")
    private Integer cpu;
    @JsonProperty("gpu")
    private Integer gpu;
    @JsonProperty("gpuType")
    private List<GpuType> gpuType;
    @JsonProperty("memory")
    private Integer memory;
    @JsonProperty("disk")
    private Integer disk;

    private List<Integer> cpuPerNode;
    private List<Integer> memoryPerNode;
    private List<Integer> diskPerNode;

    /** Rank-ordered CPU allocations; mutually exclusive with scalar CPU. */
    public List<Integer> getCpuPerNode() { return cpuPerNode; }
    public void setCpuPerNode(List<Integer> values) { cpuPerNode = values; }
    /** Rank-ordered memory allocations in GiB; mutually exclusive with scalar memory. */
    public List<Integer> getMemoryPerNode() { return memoryPerNode; }
    public void setMemoryPerNode(List<Integer> values) { memoryPerNode = values; }
    /** Rank-ordered disk allocations in GiB; mutually exclusive with scalar disk. */
    public List<Integer> getDiskPerNode() { return diskPerNode; }
    public void setDiskPerNode(List<Integer> values) { diskPerNode = values; }

    /**
     * Returns CPU allocation in cores.
     *
     * @return CPU cores
     */
    public Integer getCpu() { return cpu; }

    /**
     * Sets CPU allocation in cores.
     *
     * @param cpu CPU cores
     */
    public void setCpu(Integer cpu) { this.cpu = cpu; }

    /**
     * Returns GPU allocation.
     *
     * @return GPU units
     */
    public Integer getGpu() { return gpu; }

    /**
     * Sets GPU allocation.
     *
     * @param gpu GPU units
     */
    public void setGpu(Integer gpu) { this.gpu = gpu; }

    /**
     * Returns the preferred GPU type for the Sandbox.
     *
     * @return preferred GPU type, or {@code null} for any available type
     */
    public List<GpuType> getGpuType() { return gpuType; }

    /**
     * Sets the preferred GPU type for the Sandbox.
     *
     * @param gpuType preferred GPU type
     */
    public void setGpuType(List<GpuType> gpuType) { this.gpuType = gpuType; }

    /**
     * Returns memory allocation in GiB.
     *
     * @return memory allocation
     */
    public Integer getMemory() { return memory; }

    /**
     * Sets memory allocation in GiB.
     *
     * @param memory memory allocation
     */
    public void setMemory(Integer memory) { this.memory = memory; }

    /**
     * Returns disk allocation in GiB.
     *
     * @return disk allocation
     */
    public Integer getDisk() { return disk; }

    /**
     * Sets disk allocation in GiB.
     *
     * @param disk disk allocation
     */
    public void setDisk(Integer disk) { this.disk = disk; }
}
