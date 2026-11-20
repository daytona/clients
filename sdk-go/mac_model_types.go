// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// MacResources describes physical capacity or usage in CPU cores and MiB.
type MacResources struct {
	// Cores counts physical CPU cores.
	Cores int64
	// MemoryMiB is memory in MiB.
	MemoryMiB int64
	// DiskMiB is sandbox disk capacity or usage in MiB.
	DiskMiB int64
}

// MacModel is a registered Mac model and its default sandbox size.
type MacModel struct {
	// ID is the model slug accepted by Reservations.Create.
	ID string
	// Name is the display name.
	Name string
	// Cores counts physical CPU cores.
	Cores int64
	// MemoryMiB is physical memory in MiB.
	MemoryMiB int64
	// DiskMiB is physical SSD capacity in MiB.
	DiskMiB int64
	// SandboxDiskMiB is disk room available to sandboxes in MiB.
	SandboxDiskMiB int64
	// MaxSandboxes is the maximum concurrent sandbox count.
	MaxSandboxes int64
	// DefaultSize is nil when no default sandbox size is available.
	DefaultSize *MacResources
}

// MacPrice is the price of reserving one Mac for one billing period.
type MacPrice struct {
	// CentsPerHour is the hourly price in currency cents.
	CentsPerHour float64
	// PeriodCents is the price of one complete period in currency cents.
	PeriodCents int64
	// Period is the billing-period duration.
	Period time.Duration
	// Currency is the price's currency code.
	Currency string
}

// MacCatalogModel combines a registered model, availability and optional price.
type MacCatalogModel struct {
	// Model describes the physical Mac and sandbox defaults.
	Model MacModel
	// Available is advisory; another purchase can consume this capacity.
	Available int64
	// Price is nil when pricing is unavailable.
	Price *MacPrice
}

// MacCatalogRegion describes the models offered in one region.
type MacCatalogRegion struct {
	// ID is the region identifier.
	ID string
	// DefaultModel is the region's default model slug.
	DefaultModel string
	// Models contains model availability and prices.
	Models []MacCatalogModel
}

// MacCatalog describes Mac availability and prices by region.
type MacCatalog struct {
	// Regions contains matching regions.
	Regions []MacCatalogRegion
}

// MacCatalogOptions selects the optional catalog region filter.
type MacCatalogOptions struct {
	// Region filters the catalog; empty includes every region.
	Region string
}
