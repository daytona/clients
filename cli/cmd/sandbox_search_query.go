// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/url"
	"strconv"
	"strings"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
)

func searchQueryValues(params gen.SearchSandboxesParams) url.Values {
	query := url.Values{}
	setQueryString(query, "q", params.Q)
	setQueryString(query, "name_prefix", params.NamePrefix)
	setQueryString(query, "id_prefix", params.IdPrefix)
	setQuerySlice(query, "status", params.Status)
	setQuerySlice(query, "class", params.Class)
	setQueryString(query, "region_ids", params.RegionIds)
	setQueryString(query, "image_ref", params.ImageRef)
	setQueryString(query, "image_ref_prefix", params.ImageRefPrefix)
	setQueryString(query, "labels", params.Labels)
	setQueryString(query, "forked_from", params.ForkedFrom)
	setQueryTime(query, "created_start_at", params.CreatedStartAt)
	setQueryTime(query, "created_end_at", params.CreatedEndAt)
	setQueryTime(query, "updated_start_at", params.UpdatedStartAt)
	setQueryTime(query, "updated_end_at", params.UpdatedEndAt)
	setQueryTime(query, "last_activity_start_at", params.LastActivityStartAt)
	setQueryTime(query, "last_activity_end_at", params.LastActivityEndAt)
	setQueryString(query, "sort", params.Sort)
	setQueryString(query, "order", params.Order)
	if params.Spot != nil {
		query.Set("spot", strconv.FormatBool(*params.Spot))
	}
	setQueryFloat(query, "min_cores", params.MinCores)
	setQueryFloat(query, "max_cores", params.MaxCores)
	setQueryInt(query, "min_memory_mib", params.MinMemoryMib)
	setQueryInt(query, "max_memory_mib", params.MaxMemoryMib)
	setQueryInt(query, "min_disk_mib", params.MinDiskMib)
	setQueryInt(query, "max_disk_mib", params.MaxDiskMib)
	setQueryInt(query, "limit", params.Limit)
	setQueryString(query, "cursor", params.Cursor)
	return query
}

func setQueryString[T ~string](query url.Values, key string, value *T) {
	if value == nil || *value == "" {
		return
	}
	query.Set(key, string(*value))
}

func setQuerySlice[T ~string](query url.Values, key string, values *[]T) {
	if values == nil || len(*values) == 0 {
		return
	}
	encoded := make([]string, len(*values))
	for index, value := range *values {
		encoded[index] = string(value)
	}
	query.Set(key, strings.Join(encoded, ","))
}

func setQueryTime(query url.Values, key string, value *time.Time) {
	if value == nil {
		return
	}
	query.Set(key, value.Format(time.RFC3339Nano))
}

func setQueryInt(query url.Values, key string, value *int64) {
	if value == nil {
		return
	}
	query.Set(key, strconv.FormatInt(*value, 10))
}

func setQueryFloat(query url.Values, key string, value *float64) {
	if value == nil {
		return
	}
	query.Set(key, strconv.FormatFloat(*value, 'f', -1, 64))
}
