// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"math"
	"net/url"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

func searchQuery(options SearchSandboxesOptions) (url.Values, error) {
	query, err := pageQuery(options.Cursor, options.Limit)
	if err != nil {
		return nil, err
	}
	for _, entry := range []struct {
		name  string
		value string
	}{
		{"q", options.Q},
		{"name_prefix", options.NamePrefix},
		{"id_prefix", options.IDPrefix},
		{"image_ref", options.ImageRef},
		{"image_ref_prefix", options.ImageRefPrefix},
		{"forked_from", options.ForkedFrom},
	} {
		if !utf8.ValidString(entry.value) {
			return nil, badRequest(entry.name + " must be valid UTF-8")
		}
		if entry.value != "" {
			query.Set(entry.name, entry.value)
		}
	}
	if fieldErr := searchSequences(query, options); fieldErr != nil {
		return nil, fieldErr
	}
	if fieldErr := searchBounds(query, options); fieldErr != nil {
		return nil, fieldErr
	}
	if fieldErr := searchOrdering(query, options); fieldErr != nil {
		return nil, fieldErr
	}
	if options.Labels != nil {
		if len(options.Labels) == 0 {
			return nil, badRequest("labels must name at least one pair")
		}
		if _, empty := options.Labels[""]; empty {
			return nil, badRequest("label keys must not be empty")
		}
		encoded, encodeErr := json.Marshal(options.Labels)
		if encodeErr != nil {
			return nil, wrapJSONError(encodeErr)
		}
		query.Set("labels", string(encoded))
	}
	if options.Spot != nil {
		query.Set("spot", strconv.FormatBool(*options.Spot))
	}
	if options.Persistent != nil {
		query.Set("persistent", strconv.FormatBool(*options.Persistent))
	}
	return query, nil
}

func searchSequences(query url.Values, options SearchSandboxesOptions) error {
	if options.States != nil {
		wire, err := stateFilter(options.States)
		if err != nil {
			return err
		}
		query.Set("status", wire)
	}
	if options.Classes != nil {
		if len(options.Classes) == 0 {
			return badRequest("classes must name at least one value")
		}
		values := make([]string, 0, len(options.Classes))
		for _, class := range options.Classes {
			switch class {
			case SandboxClassLinux, SandboxClassMacOS:
				values = append(values, string(class))
			case SandboxClassUnknown:
				fallthrough
			default:
				return badRequest("classes must be linux or macos")
			}
		}
		query.Set("class", strings.Join(values, ","))
	}
	if options.Regions != nil {
		if len(options.Regions) == 0 {
			return badRequest("regions must name at least one value")
		}
		for _, value := range options.Regions {
			if value == "" || strings.Contains(value, ",") || !utf8.ValidString(value) {
				return badRequest("regions entries must be non-empty strings without commas")
			}
		}
		query.Set("region_ids", strings.Join(options.Regions, ","))
	}
	return nil
}

func stateFilter(states []SandboxState) (string, error) {
	if len(states) == 0 {
		return "", badRequest("states must name at least one value")
	}
	var wire []string
	for _, state := range states {
		values, err := stateWire(state)
		if err != nil {
			return "", err
		}
		for _, value := range values {
			if !slices.Contains(wire, value) {
				wire = append(wire, value)
			}
		}
	}
	return strings.Join(wire, ","), nil
}

func searchBounds(query url.Values, options SearchSandboxesOptions) error {
	for _, entry := range []struct {
		name  string
		value *float64
	}{
		{"min_cores", options.MinCores}, {"max_cores", options.MaxCores},
	} {
		if entry.value == nil {
			continue
		}
		if math.IsInf(*entry.value, 0) || math.IsNaN(*entry.value) {
			return badRequest(entry.name + " must be finite")
		}
		query.Set(entry.name, strconv.FormatFloat(*entry.value, 'g', -1, 64))
	}
	for _, entry := range []struct {
		name  string
		value *int64
	}{
		{"min_memory_mib", options.MinMemoryMiB},
		{"max_memory_mib", options.MaxMemoryMiB},
		{"min_disk_mib", options.MinDiskMiB},
		{"max_disk_mib", options.MaxDiskMiB},
	} {
		if entry.value == nil {
			continue
		}
		if *entry.value > maxSafeInteger || *entry.value < -maxSafeInteger {
			return badRequest(entry.name + " must be a safe integer")
		}
		query.Set(entry.name, strconv.FormatInt(*entry.value, 10))
	}
	for _, entry := range []struct {
		name  string
		value time.Time
	}{
		{"created_start_at", options.CreatedStartAt},
		{"created_end_at", options.CreatedEndAt},
		{"updated_start_at", options.UpdatedStartAt},
		{"updated_end_at", options.UpdatedEndAt},
		{"last_activity_start_at", options.LastActivityStartAt},
		{"last_activity_end_at", options.LastActivityEndAt},
	} {
		if entry.value.IsZero() {
			continue
		}
		utc := entry.value.UTC()
		if utc.Year() < 1 || utc.Year() > 9999 {
			return badRequest(entry.name + " must be in years 0001-9999")
		}
		query.Set(entry.name, utc.Format(time.RFC3339Nano))
	}
	return nil
}

func searchOrdering(query url.Values, options SearchSandboxesOptions) error {
	switch options.Sort {
	case "":
	case SearchSortCreatedAt, SearchSortUpdatedAt, SearchSortLastActivityAt, SearchSortName,
		SearchSortCores, SearchSortMemoryMiB, SearchSortDiskMiB:
		query.Set("sort", string(options.Sort))
	default:
		return badRequest("invalid search sort")
	}
	switch options.Order {
	case "":
	case SortOrderAscending, SortOrderDescending:
		query.Set("order", string(options.Order))
	default:
		return badRequest("invalid search order")
	}
	return nil
}
