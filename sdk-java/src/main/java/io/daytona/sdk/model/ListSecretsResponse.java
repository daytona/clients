// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.ArrayList;
import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
/**
 * Paginated list response for Secrets.
 */
public class ListSecretsResponse {
    @JsonProperty("items")
    private List<Secret> items;
    @JsonProperty("total")
    private Integer total;
    @JsonProperty("nextCursor")
    private String nextCursor;

    /**
     * Returns Secret items in the current page.
     *
     * @return page items
     */
    public List<Secret> getItems() { return items == null ? new ArrayList<Secret>() : items; }

    /**
     * Sets Secret items in the current page.
     *
     * @param items page items
     */
    public void setItems(List<Secret> items) { this.items = items; }

    /**
     * Returns total Secret count matching the filters.
     *
     * @return total secrets
     */
    public int getTotal() { return total == null ? 0 : total; }

    /**
     * Sets total Secret count matching the filters.
     *
     * @param total total secrets
     */
    public void setTotal(Integer total) { this.total = total; }

    /**
     * Returns the cursor for the next page of results, or {@code null} when there are no more pages.
     *
     * @return next page cursor, or {@code null} when there are no more pages
     */
    public String getNextCursor() { return nextCursor; }

    /**
     * Sets the cursor for the next page of results.
     *
     * @param nextCursor next page cursor, or {@code null} when there are no more pages
     */
    public void setNextCursor(String nextCursor) { this.nextCursor = nextCursor; }
}
