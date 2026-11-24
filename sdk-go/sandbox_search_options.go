// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// SearchSandboxesOptions filters sandboxes. Filters combine with AND; values within
// States, Classes and Regions combine with OR. Nil slices/maps omit filters;
// explicit empty ones are invalid. Memory/disk bounds are MiB, CPU bounds cores.
// Zero timestamps omit bounds; starts are inclusive and ends exclusive.
type SearchSandboxesOptions struct {
	// Q is free text or an exact ID.
	Q string
	// NamePrefix filters by name prefix.
	NamePrefix string
	// IDPrefix filters by ID prefix.
	IDPrefix string
	// States accepts known SDK states and expands them to server statuses.
	States []SandboxState
	// Classes selects known operating-system classes.
	Classes []SandboxClass
	// Regions selects region IDs without comma characters.
	Regions []string
	// ImageRef selects an exact image reference.
	ImageRef string
	// ImageRefPrefix selects an image-reference prefix.
	ImageRefPrefix string
	// Labels matches containment of all supplied pairs.
	Labels map[string]string
	// Spot selects interruptible capacity; nil omits the filter.
	Spot *bool
	// ForkedFrom selects forks of a sandbox.
	ForkedFrom string
	// MinCores is the inclusive minimum CPU limit in cores.
	MinCores *float64
	// MaxCores is the inclusive maximum CPU limit in cores.
	MaxCores *float64
	// MinMemoryMiB is an inclusive memory bound in MiB.
	MinMemoryMiB *int64
	// MaxMemoryMiB is an inclusive memory bound in MiB.
	MaxMemoryMiB *int64
	// MinDiskMiB is an inclusive disk bound in MiB.
	MinDiskMiB *int64
	// MaxDiskMiB is an inclusive disk bound in MiB.
	MaxDiskMiB *int64
	// CreatedStartAt is an inclusive creation bound.
	CreatedStartAt time.Time
	// CreatedEndAt is an exclusive creation bound.
	CreatedEndAt time.Time
	// UpdatedStartAt is an inclusive update bound.
	UpdatedStartAt time.Time
	// UpdatedEndAt is an exclusive update bound.
	UpdatedEndAt time.Time
	// LastActivityStartAt is an inclusive activity bound.
	LastActivityStartAt time.Time
	// LastActivityEndAt is an exclusive activity bound.
	LastActivityEndAt time.Time
	// Sort defaults to creation time.
	Sort SearchSort
	// Order defaults to descending.
	Order SortOrder
	// Limit is 1–200; zero uses the server default.
	Limit int
	// Cursor is the opaque continuation cursor.
	Cursor string
}

// SearchSort selects the search ordering field. Zero uses created_at.
type SearchSort string

const (
	// SearchSortCreatedAt sorts by creation.
	SearchSortCreatedAt SearchSort = "created_at"
	// SearchSortUpdatedAt sorts by last update.
	SearchSortUpdatedAt SearchSort = "updated_at"
	// SearchSortLastActivityAt sorts by last activity.
	SearchSortLastActivityAt SearchSort = "last_activity_at"
	// SearchSortName sorts by name.
	SearchSortName SearchSort = "name"
	// SearchSortCores sorts by CPU limit in cores.
	SearchSortCores SearchSort = "cores"
	// SearchSortMemoryMiB sorts by memory in MiB.
	SearchSortMemoryMiB SearchSort = "memory_mib"
	// SearchSortDiskMiB sorts by disk in MiB.
	SearchSortDiskMiB SearchSort = "disk_mib"
)

// SortOrder selects ascending or descending results. Zero uses descending.
type SortOrder string

const (
	// SortOrderAscending requests smallest/oldest first.
	SortOrderAscending SortOrder = "asc"
	// SortOrderDescending requests largest/newest first.
	SortOrderDescending SortOrder = "desc"
)
