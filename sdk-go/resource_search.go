// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/url"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

// SearchSecretsOptions filters secret metadata. Filters combine with AND. Zero
// timestamps omit bounds; starts are inclusive and ends exclusive.
type SearchSecretsOptions struct {
	// Q is case-insensitive free text over the name and description.
	Q string
	// Name selects an exact name.
	Name string
	// NamePrefix filters by name prefix.
	NamePrefix string
	// AllowedDomain matches one allowed domain exactly.
	AllowedDomain string
	// CreatedStartAt is an inclusive creation bound.
	CreatedStartAt time.Time
	// CreatedEndAt is an exclusive creation bound.
	CreatedEndAt time.Time
	// UpdatedStartAt is an inclusive update bound.
	UpdatedStartAt time.Time
	// UpdatedEndAt is an exclusive update bound.
	UpdatedEndAt time.Time
	// Sort defaults to name.
	Sort SecretSearchSort
	// Order defaults to ascending.
	Order SortOrder
	// Limit is 1–200; zero uses the server default.
	Limit int
	// Cursor is the opaque continuation cursor.
	Cursor string
}

// SecretSearchSort selects the secret search ordering field.
type SecretSearchSort string

const (
	// SecretSearchSortName sorts by name.
	SecretSearchSortName SecretSearchSort = "name"
	// SecretSearchSortCreatedAt sorts by creation.
	SecretSearchSortCreatedAt SecretSearchSort = "created_at"
	// SecretSearchSortUpdatedAt sorts by last update.
	SecretSearchSortUpdatedAt SecretSearchSort = "updated_at"
)

// SearchVolumesOptions filters volumes. Filters combine with AND; values within
// Types and Statuses combine with OR. Nil slices omit filters; explicit empty
// ones are invalid. Zero timestamps omit bounds; starts are inclusive and ends
// exclusive.
type SearchVolumesOptions struct {
	// Q is case-insensitive free text over the name, or an exact ID.
	Q string
	// Name selects an exact name.
	Name string
	// NamePrefix filters by name prefix.
	NamePrefix string
	// Types selects disk or bucket volumes.
	Types []VolumeType
	// Statuses selects ready, deleting or error volumes.
	Statuses []VolumeStatus
	// Imported selects imported (true) or native (false) volumes; nil returns both.
	Imported *bool
	// CreatedStartAt is an inclusive creation bound.
	CreatedStartAt time.Time
	// CreatedEndAt is an exclusive creation bound.
	CreatedEndAt time.Time
	// UpdatedStartAt is an inclusive update bound.
	UpdatedStartAt time.Time
	// UpdatedEndAt is an exclusive update bound.
	UpdatedEndAt time.Time
	// LastUsedStartAt is an inclusive last-use bound.
	LastUsedStartAt time.Time
	// LastUsedEndAt is an exclusive last-use bound.
	LastUsedEndAt time.Time
	// Sort defaults to creation time.
	Sort VolumeSearchSort
	// Order defaults to descending.
	Order SortOrder
	// Limit is 1–200; zero uses the server default.
	Limit int
	// Cursor is the opaque continuation cursor.
	Cursor string
}

// VolumeSearchSort selects the volume search ordering field.
type VolumeSearchSort string

const (
	// VolumeSearchSortCreatedAt sorts by creation.
	VolumeSearchSortCreatedAt VolumeSearchSort = "created_at"
	// VolumeSearchSortUpdatedAt sorts by last update.
	VolumeSearchSortUpdatedAt VolumeSearchSort = "updated_at"
	// VolumeSearchSortName sorts by name.
	VolumeSearchSortName VolumeSearchSort = "name"
	// VolumeSearchSortUsedBytes sorts by reported usage.
	VolumeSearchSortUsedBytes VolumeSearchSort = "used_bytes"
	// VolumeSearchSortLastUsedAt sorts by last use.
	VolumeSearchSortLastUsedAt VolumeSearchSort = "last_used_at"
	// VolumeSearchSortSizeGiB sorts by capacity.
	VolumeSearchSortSizeGiB VolumeSearchSort = "size_gib"
)

// Search returns one page of secret metadata and its hit count. Results are
// eventually consistent. Unlike List it never falls back: ErrSearchNotConfigured
// and ErrSearchUnavailable report those deployment conditions.
func (s *Secrets) Search(ctx context.Context, opts *SearchSecretsOptions) (*SearchPage[Secret], error) {
	options := SearchSecretsOptions{}
	if opts != nil {
		options = *opts
	}
	query, err := resourceSearchQuery(options.Cursor, options.Limit, []searchText{
		{"q", options.Q},
		{"name", options.Name},
		{"name_prefix", options.NamePrefix},
		{"allowed_domain", options.AllowedDomain},
	}, []searchTime{
		{"created_start_at", options.CreatedStartAt},
		{"created_end_at", options.CreatedEndAt},
		{"updated_start_at", options.UpdatedStartAt},
		{"updated_end_at", options.UpdatedEndAt},
	})
	if err != nil {
		return nil, err
	}
	switch options.Sort {
	case "":
	case SecretSearchSortName, SecretSearchSortCreatedAt, SecretSearchSortUpdatedAt:
		query.Set("sort", string(options.Sort))
	default:
		return nil, badRequest("Sort must be name, created_at or updated_at")
	}
	if err = searchOrder(query, options.Order); err != nil {
		return nil, err
	}
	var raw JSONValue
	if requestErr := s.client.api().do(ctx, apiRequest{
		Path: "/secrets/search", Query: query, Result: &raw,
	}); requestErr != nil {
		return nil, requestErr
	}
	return resourceSearchPage(raw, "secrets", parseSecret)
}

// Search returns one page of volumes and its hit count. Results are eventually
// consistent. Unlike List it never falls back: ErrSearchNotConfigured and
// ErrSearchUnavailable report those deployment conditions.
func (v *Volumes) Search(ctx context.Context, opts *SearchVolumesOptions) (*SearchPage[Volume], error) {
	options := SearchVolumesOptions{}
	if opts != nil {
		options = *opts
	}
	query, err := resourceSearchQuery(options.Cursor, options.Limit, []searchText{
		{"q", options.Q}, {"name", options.Name}, {"name_prefix", options.NamePrefix},
	}, []searchTime{
		{"created_start_at", options.CreatedStartAt},
		{"created_end_at", options.CreatedEndAt},
		{"updated_start_at", options.UpdatedStartAt},
		{"updated_end_at", options.UpdatedEndAt},
		{"last_used_start_at", options.LastUsedStartAt},
		{"last_used_end_at", options.LastUsedEndAt},
	})
	if err != nil {
		return nil, err
	}
	if err = volumeSearchFilters(query, options); err != nil {
		return nil, err
	}
	switch options.Sort {
	case "":
	case VolumeSearchSortCreatedAt, VolumeSearchSortUpdatedAt, VolumeSearchSortName,
		VolumeSearchSortUsedBytes, VolumeSearchSortLastUsedAt, VolumeSearchSortSizeGiB:
		query.Set("sort", string(options.Sort))
	default:
		return nil, badRequest("Sort must be created_at, updated_at, name, used_bytes, last_used_at or size_gib")
	}
	if err = searchOrder(query, options.Order); err != nil {
		return nil, err
	}
	var raw JSONValue
	if requestErr := v.client.api().do(ctx, apiRequest{
		Path: "/volumes/search", Query: query, Result: &raw,
	}); requestErr != nil {
		return nil, requestErr
	}
	return resourceSearchPage(raw, "volumes", parseVolume)
}

func volumeSearchFilters(query url.Values, options SearchVolumesOptions) error {
	if options.Types != nil {
		values := make([]string, 0, len(options.Types))
		for _, volumeType := range options.Types {
			switch volumeType {
			case VolumeTypeDisk, VolumeTypeBucket:
				values = append(values, string(volumeType))
			case VolumeTypeUnknown:
				fallthrough
			default:
				return badRequest("Types entries must be disk or bucket")
			}
		}
		if len(values) == 0 {
			return badRequest("Types must name at least one value")
		}
		query.Set("type", strings.Join(values, ","))
	}
	if options.Statuses != nil {
		values := make([]string, 0, len(options.Statuses))
		for _, status := range options.Statuses {
			switch status {
			case VolumeStatusReady, VolumeStatusDeleting, VolumeStatusError:
				values = append(values, string(status))
			case VolumeStatusDeleted, VolumeStatusUnknown:
				fallthrough
			default:
				return badRequest("Statuses entries must be ready, deleting or error")
			}
		}
		if len(values) == 0 {
			return badRequest("Statuses must name at least one value")
		}
		query.Set("status", strings.Join(values, ","))
	}
	if options.Imported != nil {
		query.Set("imported", strconv.FormatBool(*options.Imported))
	}
	return nil
}

type searchText struct{ name, value string }

type searchTime struct {
	name  string
	value time.Time
}

func resourceSearchQuery(cursor string, limit int, texts []searchText, times []searchTime) (url.Values, error) {
	query, err := pageQuery(cursor, limit)
	if err != nil {
		return nil, err
	}
	for _, entry := range texts {
		if !utf8.ValidString(entry.value) {
			return nil, badRequest(entry.name + " must be valid UTF-8")
		}
		if entry.value != "" {
			query.Set(entry.name, entry.value)
		}
	}
	for _, entry := range times {
		if entry.value.IsZero() {
			continue
		}
		utc := entry.value.UTC()
		if utc.Year() < 1 || utc.Year() > 9999 {
			return nil, badRequest(entry.name + " must be in years 0001-9999")
		}
		query.Set(entry.name, utc.Format(time.RFC3339Nano))
	}
	return query, nil
}

func searchOrder(query url.Values, order SortOrder) error {
	switch order {
	case "":
	case SortOrderAscending, SortOrderDescending:
		query.Set("order", string(order))
	default:
		return badRequest("Order must be asc or desc")
	}
	return nil
}

func resourceSearchPage[T any](raw JSONValue, key string, parse func(JSONValue) (T, error)) (*SearchPage[T], error) {
	page, err := resourcePage(raw, key, parse)
	if err != nil {
		return nil, err
	}
	object, err := parseObject(raw, key+" search")
	if err != nil {
		return nil, err
	}
	total, err := integerField(object, "total", true)
	if err != nil {
		return nil, err
	}
	if total < 0 {
		return nil, badRequest("search response has a negative total")
	}
	rawRelation, err := field[string](object, "total_relation", true)
	if err != nil {
		return nil, err
	}
	relation := TotalRelationUnknown
	switch rawRelation {
	case "eq":
		relation = TotalRelationEqual
	case "gte":
		relation = TotalRelationGreaterOrEqual
	}
	return &SearchPage[T]{Page: *page, Total: total, TotalRelation: relation, RawTotalRelation: rawRelation}, nil
}

// resourceListReader reads a secret or volume list search-first. Besides the
// fallbacks artifactListReader applies (404, SEARCH_NOT_CONFIGURED, a 410 on a
// remembered fallback), it falls back on a 200 that is not a page: an API that
// predates the search route answers GET /<kind>/search with the resource
// named "search".
type resourceListReader struct {
	reader artifactListReader
	key    string
}

func (r resourceListReader) read(ctx context.Context, query url.Values) (JSONValue, error) {
	searched := !r.reader.fallback.Load()
	raw, err := r.reader.read(ctx, query)
	if err != nil || !searched || r.reader.fallback.Load() || isResourcePage(raw, r.key) {
		return raw, err
	}
	r.reader.fallback.Store(true)
	var list JSONValue
	err = r.reader.client.api().do(ctx, apiRequest{Path: r.reader.path, Query: query, Result: &list})
	return list, err
}

func isResourcePage(raw JSONValue, key string) bool {
	object, err := parseObject(raw, key+" list")
	if err != nil {
		return false
	}
	_, err = field[[]JSONValue](object, key, true)
	return err == nil
}

// refuseSearchName rejects the exact name "search": GET, PUT and DELETE on
// /secrets/search and /volumes/search reach the search route, so a secret or
// volume of that name cannot be addressed by name. "SEARCH" stays valid.
func refuseSearchName(name, kind string) error {
	if name == "search" {
		return badRequest("'search' cannot be used as a " + kind + " name; it is reserved for the search route")
	}
	return nil
}

// resourceSegment is pathSegment for a secret or volume name, which may not be
// the reserved name "search".
func resourceSegment(name, kind string) (string, error) {
	if err := refuseSearchName(name, kind); err != nil {
		return "", err
	}
	return pathSegment(name)
}
