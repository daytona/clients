// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true)
/**
 * Volume metadata returned by Daytona APIs.
 */
public class Volume {
    @JsonProperty("id")
    private String id;
    @JsonProperty("name")
    private String name;
    @JsonProperty("type")
    private String type;
    @JsonProperty("sizeInGb")
    private java.math.BigDecimal sizeInGb;
    @JsonProperty("region")
    private String region;
    @JsonProperty("shared")
    private Boolean shared;
    @JsonProperty("state")
    private String state;

    /**
     * Returns volume identifier.
     *
     * @return volume ID
     */
    public String getId() { return id; }

    /**
     * Sets volume identifier.
     *
     * @param id volume ID
     */
    public void setId(String id) { this.id = id; }

    /**
     * Returns volume name.
     *
     * @return volume name
     */
    public String getName() { return name; }

    /**
     * Sets volume name.
     *
     * @param name volume name
     */
    public void setName(String name) { this.name = name; }

    /**
     * Returns volume type.
     *
     * @return volume type (legacy, hotmount, or blockmount)
     */
    public String getType() { return type; }

    /**
     * Sets volume type.
     *
     * @param type volume type
     */
    public void setType(String type) { this.type = type; }

    /**
     * Returns the per-sandbox scratch quota in GB.
     *
     * @return size in GB, or null for volume types that do not use it
     */
    public java.math.BigDecimal getSizeInGb() { return sizeInGb; }

    /**
     * Sets the per-sandbox scratch quota in GB.
     *
     * @param sizeInGb size in GB
     */
    public void setSizeInGb(java.math.BigDecimal sizeInGb) { this.sizeInGb = sizeInGb; }

    /**
     * Returns the hotmount region the volume lives in.
     *
     * @return region id, or null for non-hotmount volumes
     */
    public String getRegion() { return region; }

    /**
     * Sets the hotmount region the volume lives in.
     *
     * @param region region id
     */
    public void setRegion(String region) { this.region = region; }

    /**
     * Returns the hotmount sharing mode.
     *
     * @return sharing mode, or null for non-hotmount volumes
     */
    public Boolean getShared() { return shared; }

    /**
     * Sets the hotmount sharing mode.
     *
     * @param shared sharing mode
     */
    public void setShared(Boolean shared) { this.shared = shared; }

    /**
     * Returns volume state.
     *
     * @return lifecycle state
     */
    public String getState() { return state; }

    /**
     * Sets volume state.
     *
     * @param state lifecycle state
     */
    public void setState(String state) { this.state = state; }
}
