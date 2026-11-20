// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// MacReservation is a caller-owned observation of a dedicated-Mac reservation.
// Optional timestamps are zero when absent. Reservations are billed per period.
type MacReservation struct {
	// ID identifies the reservation.
	ID string
	// OrgID identifies the owning organization.
	OrgID string
	// ProjectID identifies the owning project.
	ProjectID string
	// Region is the reservation's region.
	Region string
	// AutoRenew reports whether the reservation renews automatically.
	AutoRenew bool
	// SlotsUsed counts occupied sandbox slots.
	SlotsUsed int64
	// Model is the model slug, or empty when unreported.
	Model string
	// Capacity is nil when physical capacity is unreported.
	Capacity *MacResources
	// ResourcesUsed is the current resource usage.
	ResourcesUsed MacResources
	// PeriodN is the server's billing-period number.
	PeriodN int64
	// Period is the billing-period duration.
	Period time.Duration
	// CreatedAt is the creation timestamp.
	CreatedAt time.Time
	// ActivatedAt is the activation timestamp.
	ActivatedAt time.Time
	// PendingMacStartedAt is when Mac assignment began.
	PendingMacStartedAt time.Time
	// PeriodStartedAt is when the current period began.
	PeriodStartedAt time.Time
	// PeriodEnd is when the current period ends.
	PeriodEnd time.Time
	// ReissuedAt is the latest reissue timestamp.
	ReissuedAt time.Time
	// EndedAt is when the reservation ended.
	EndedAt time.Time
	// RenewalPending reports a pending renewal.
	RenewalPending bool
	// RenewalError is the server's renewal error, if any.
	RenewalError string
	// Status is the normalized reservation state.
	Status MacReservationStatus
	// RawStatus preserves the server state verbatim.
	RawStatus string
	// EndReason is empty when absent, otherwise normalized.
	EndReason MacReservationEndReason
	// RawEndReason preserves the server end reason verbatim.
	RawEndReason string
	// Origin is the normalized purchase origin.
	Origin MacReservationOrigin
	// RawOrigin preserves the server origin verbatim.
	RawOrigin string
}

// MacReservationBatch contains the results of a single bulk purchase.
type MacReservationBatch struct {
	// Requested is the number requested.
	Requested int64
	// Reserved is the number purchased.
	Reserved int64
	// Reservations contains the purchased reservations.
	Reservations []MacReservation
}

// CreateReservationOptions controls an optional model, renewal policy and retry key.
type CreateReservationOptions struct {
	// Model is a registered model slug; empty selects the region's default.
	// An unavailable model is never upgraded.
	Model string
	// AutoRenew overrides the organization's setting; nil uses that setting.
	AutoRenew *bool
	// IdempotencyKey is reused on retries to avoid buying a second Mac.
	// Empty generates a fresh key for this call.
	IdempotencyKey string
}

// CreateReservationsBulkOptions controls a bulk reservation purchase.
type CreateReservationsBulkOptions struct {
	// Model is a registered model slug; empty selects the region's default.
	Model string
	// AutoRenew overrides the organization's setting; nil uses that setting.
	AutoRenew *bool
	// IdempotencyKey avoids another purchase on retry; empty generates a key.
	IdempotencyKey string
	// MinCount is the fewest Macs to accept, from 1 to count; zero requires count.
	MinCount int
}

// RenewReservationOptions controls safe retries of a billed renewal.
type RenewReservationOptions struct {
	// IdempotencyKey avoids paying for another period on retry.
	// Empty generates a fresh key for this call.
	IdempotencyKey string
}

// ListReservationsOptions selects one page of reservations.
type ListReservationsOptions struct {
	// Cursor is the opaque continuation cursor.
	Cursor string
	// Limit is 1–200; zero uses the server default.
	Limit int
}
