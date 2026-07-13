// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.model;

import java.time.OffsetDateTime;

/**
 * A single point-in-time sample of historical Sandbox resource usage.
 */
public class SandboxMetrics {
    private final int cpuCount;
    private final double cpuUsedPct;
    private final long diskTotal;
    private final long diskUsed;
    private final long memTotal;
    private final long memUsed;
    private final long memCache;
    private final OffsetDateTime timestamp;

    /**
     * Creates a Sandbox metrics sample.
     *
     * @param cpuCount number of CPU cores allocated to the Sandbox
     * @param cpuUsedPct CPU utilization as a percentage of the allocated limit
     * @param diskTotal total disk space in bytes
     * @param diskUsed used disk space in bytes
     * @param memTotal total memory in bytes
     * @param memUsed used memory in bytes
     * @param memCache memory used by the page cache in bytes
     * @param timestamp timestamp of this sample
     */
    public SandboxMetrics(int cpuCount, double cpuUsedPct, long diskTotal, long diskUsed,
                          long memTotal, long memUsed, long memCache, OffsetDateTime timestamp) {
        this.cpuCount = cpuCount;
        this.cpuUsedPct = cpuUsedPct;
        this.diskTotal = diskTotal;
        this.diskUsed = diskUsed;
        this.memTotal = memTotal;
        this.memUsed = memUsed;
        this.memCache = memCache;
        this.timestamp = timestamp;
    }

    /**
     * Returns the number of CPU cores allocated to the Sandbox.
     *
     * @return CPU core count
     */
    public int getCpuCount() { return cpuCount; }

    /**
     * Returns CPU utilization as a percentage of the allocated limit.
     *
     * @return CPU utilization percentage
     */
    public double getCpuUsedPct() { return cpuUsedPct; }

    /**
     * Returns total disk space in bytes.
     *
     * @return total disk bytes
     */
    public long getDiskTotal() { return diskTotal; }

    /**
     * Returns used disk space in bytes.
     *
     * @return used disk bytes
     */
    public long getDiskUsed() { return diskUsed; }

    /**
     * Returns total memory in bytes.
     *
     * @return total memory bytes
     */
    public long getMemTotal() { return memTotal; }

    /**
     * Returns used memory in bytes.
     *
     * @return used memory bytes
     */
    public long getMemUsed() { return memUsed; }

    /**
     * Returns memory used by the page cache in bytes.
     *
     * @return page cache bytes
     */
    public long getMemCache() { return memCache; }

    /**
     * Returns the timestamp of this sample.
     *
     * @return sample timestamp
     */
    public OffsetDateTime getTimestamp() { return timestamp; }
}
