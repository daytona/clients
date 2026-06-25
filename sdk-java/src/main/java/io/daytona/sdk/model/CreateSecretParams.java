// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
/**
 * Parameters for creating a new organization-scoped Secret.
 */
public class CreateSecretParams {
    private String name;
    private String value;
    private String description;
    private List<String> hosts;

    /**
     * Returns the Secret name.
     *
     * @return Secret name; must match {@code ^[a-zA-Z_][a-zA-Z0-9_-]*$} and be unique within the organization
     */
    public String getName() { return name; }

    /**
     * Sets the Secret name.
     *
     * @param name Secret name; must match {@code ^[a-zA-Z_][a-zA-Z0-9_-]*$} and be unique within the organization
     */
    public void setName(String name) { this.name = name; }

    /**
     * Returns the plaintext Secret value.
     *
     * @return Secret value; stored encrypted and never returned by the API
     */
    public String getValue() { return value; }

    /**
     * Sets the plaintext Secret value.
     *
     * @param value Secret value; stored encrypted and never returned by the API
     */
    public void setValue(String value) { this.value = value; }

    /**
     * Returns the optional Secret description.
     *
     * @return description, or {@code null}
     */
    public String getDescription() { return description; }

    /**
     * Sets the optional Secret description.
     *
     * @param description description
     */
    public void setDescription(String description) { this.description = description; }

    /**
     * Returns the hosts the Secret value may be sent to.
     *
     * <p>Each entry is a hostname ({@code api.example.com}) or a {@code *.} wildcard
     * ({@code *.example.com}); ports are not supported. Omit to leave the Secret unrestricted.
     *
     * @return allowed hosts, or {@code null}
     */
    public List<String> getHosts() { return hosts; }

    /**
     * Sets the hosts the Secret value may be sent to.
     *
     * @param hosts allowed hosts
     */
    public void setHosts(List<String> hosts) { this.hosts = hosts; }
}
