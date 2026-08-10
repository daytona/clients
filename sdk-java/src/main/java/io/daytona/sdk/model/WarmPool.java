// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true)
/**
 * Warm pool metadata returned by Daytona APIs.
 *
 * <p>{@code currentSize} versus {@code pool} is the pool's status: {@code currentSize} is the
 * number of ready warm sandboxes, {@code pool} is the desired number. {@code errorReason} is
 * set when the pool cannot be filled.
 */
public class WarmPool {
    @JsonProperty("id")
    private String id;
    @JsonProperty("snapshot")
    private String snapshot;
    @JsonProperty("target")
    private String target;
    @JsonProperty("pool")
    private int pool;
    @JsonProperty("currentSize")
    private int currentSize;
    @JsonProperty("errorReason")
    private String errorReason;

    /**
     * Returns warm pool identifier.
     *
     * @return warm pool ID
     */
    public String getId() { return id; }

    /**
     * Sets warm pool identifier.
     *
     * @param id warm pool ID
     */
    public void setId(String id) { this.id = id; }

    /**
     * Returns the snapshot the pool keeps warm sandboxes for.
     *
     * @return snapshot ID or name
     */
    public String getSnapshot() { return snapshot; }

    /**
     * Sets the snapshot the pool keeps warm sandboxes for.
     *
     * @param snapshot snapshot ID or name
     */
    public void setSnapshot(String snapshot) { this.snapshot = snapshot; }

    /**
     * Returns the target region of the pool.
     *
     * @return target region
     */
    public String getTarget() { return target; }

    /**
     * Sets the target region of the pool.
     *
     * @param target target region
     */
    public void setTarget(String target) { this.target = target; }

    /**
     * Returns the desired number of warm sandboxes.
     *
     * @return desired pool size
     */
    public int getPool() { return pool; }

    /**
     * Sets the desired number of warm sandboxes.
     *
     * @param pool desired pool size
     */
    public void setPool(int pool) { this.pool = pool; }

    /**
     * Returns the current number of ready warm sandboxes in the pool.
     *
     * @return current pool size
     */
    public int getCurrentSize() { return currentSize; }

    /**
     * Sets the current number of ready warm sandboxes in the pool.
     *
     * @param currentSize current pool size
     */
    public void setCurrentSize(int currentSize) { this.currentSize = currentSize; }

    /**
     * Returns the reason the pool cannot be filled, if any.
     *
     * @return error reason or {@code null}
     */
    public String getErrorReason() { return errorReason; }

    /**
     * Sets the reason the pool cannot be filled.
     *
     * @param errorReason error reason
     */
    public void setErrorReason(String errorReason) { this.errorReason = errorReason; }
}
