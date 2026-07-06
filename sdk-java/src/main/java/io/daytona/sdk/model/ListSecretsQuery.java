// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.model;

/**
 * Query parameters for filtering, sorting, and paginating when listing Secrets.
 */
public class ListSecretsQuery {
    /** Pagination cursor from a previous response */
    private String cursor;
    /** Number of results per page (1-200, defaults to 100) */
    private Integer limit;
    /** Filter by partial name match */
    private String name;
    /** Field to sort by: {@code name}, {@code createdAt}, or {@code updatedAt} (defaults to {@code createdAt}) */
    private String sort;
    /** Sort direction: {@code asc} or {@code desc} (defaults to {@code desc}) */
    private String order;

    public String getCursor() { return cursor; }
    public void setCursor(String cursor) { this.cursor = cursor; }
    public Integer getLimit() { return limit; }
    public void setLimit(Integer limit) { this.limit = limit; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getSort() { return sort; }
    public void setSort(String sort) { this.sort = sort; }
    public String getOrder() { return order; }
    public void setOrder(String order) { this.order = order; }
}
