// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
/**
 * Organization-scoped Secret metadata returned by Daytona APIs.
 *
 * <p>The plaintext {@code value} is write-only and is never returned by the API. When a Secret is
 * referenced from a Sandbox, the injected environment variable holds the opaque {@link #getPlaceholder()}
 * token, not the real value; the real value is substituted transparently on outbound requests to the
 * Secret's allowed {@link #getHosts() hosts}.
 */
public class Secret {
    @JsonProperty("id")
    private String id;
    @JsonProperty("name")
    private String name;
    @JsonProperty("description")
    private String description;
    @JsonProperty("placeholder")
    private String placeholder;
    @JsonProperty("hosts")
    private List<String> hosts;
    @JsonProperty("createdAt")
    private String createdAt;
    @JsonProperty("updatedAt")
    private String updatedAt;

    /**
     * Returns Secret identifier.
     *
     * @return Secret ID
     */
    public String getId() { return id; }

    /**
     * Sets Secret identifier.
     *
     * @param id Secret ID
     */
    public void setId(String id) { this.id = id; }

    /**
     * Returns Secret name, unique within the organization.
     *
     * @return Secret name
     */
    public String getName() { return name; }

    /**
     * Sets Secret name.
     *
     * @param name Secret name
     */
    public void setName(String name) { this.name = name; }

    /**
     * Returns Secret description.
     *
     * @return description, or {@code null}
     */
    public String getDescription() { return description; }

    /**
     * Sets Secret description.
     *
     * @param description description
     */
    public void setDescription(String description) { this.description = description; }

    /**
     * Returns the opaque placeholder token injected as the env var value in a Sandbox.
     *
     * @return placeholder token
     */
    public String getPlaceholder() { return placeholder; }

    /**
     * Sets the opaque placeholder token.
     *
     * @param placeholder placeholder token
     */
    public void setPlaceholder(String placeholder) { this.placeholder = placeholder; }

    /**
     * Returns the hosts the Secret value may be sent to.
     *
     * @return list of allowed hosts (may be empty)
     */
    public List<String> getHosts() { return hosts; }

    /**
     * Sets the hosts the Secret value may be sent to.
     *
     * @param hosts allowed hosts
     */
    public void setHosts(List<String> hosts) { this.hosts = hosts; }

    /**
     * Returns the creation timestamp.
     *
     * @return creation time
     */
    public String getCreatedAt() { return createdAt; }

    /**
     * Sets the creation timestamp.
     *
     * @param createdAt creation time
     */
    public void setCreatedAt(String createdAt) { this.createdAt = createdAt; }

    /**
     * Returns the last-update timestamp.
     *
     * @return last update time
     */
    public String getUpdatedAt() { return updatedAt; }

    /**
     * Sets the last-update timestamp.
     *
     * @param updatedAt last update time
     */
    public void setUpdatedAt(String updatedAt) { this.updatedAt = updatedAt; }
}
