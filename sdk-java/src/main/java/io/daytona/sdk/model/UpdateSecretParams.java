// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
/**
 * Parameters for updating an existing organization-scoped Secret.
 *
 * <p>Omitted ({@code null}) fields are left unchanged.
 */
public class UpdateSecretParams {
    private String value;
    private String description;
    private List<String> hosts;

    /**
     * Returns the replacement Secret value.
     *
     * @return new plaintext value, or {@code null} to leave unchanged
     */
    public String getValue() { return value; }

    /**
     * Sets the replacement Secret value.
     *
     * @param value new plaintext value; stored encrypted and never returned by the API
     */
    public void setValue(String value) { this.value = value; }

    /**
     * Returns the Secret description.
     *
     * @return description, or {@code null} to leave unchanged
     */
    public String getDescription() { return description; }

    /**
     * Sets the Secret description.
     *
     * @param description description
     */
    public void setDescription(String description) { this.description = description; }

    /**
     * Returns the hosts the Secret value may be sent to.
     *
     * <p>Same constraints as {@link CreateSecretParams#getHosts()}.
     *
     * @return allowed hosts, or {@code null} to leave unchanged
     */
    public List<String> getHosts() { return hosts; }

    /**
     * Sets the hosts the Secret value may be sent to.
     *
     * @param hosts allowed hosts
     */
    public void setHosts(List<String> hosts) { this.hosts = hosts; }
}
