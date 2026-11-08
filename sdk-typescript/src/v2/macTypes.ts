/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type {
  MacReservationEndReason as END_REASONS,
  MacReservationOrigin as ORIGINS,
  MacReservationStatus as STATUSES,
} from './gen/api-enums.js'

/** Physical CPU cores, memory, and sandbox disk capacity or usage. */
export interface MacResources {
  readonly cores: number
  readonly memoryMib: number
  readonly diskMib: number
}

/** A registered Mac model and the default sandbox size it provides. */
export interface MacModel {
  readonly id: string
  readonly name: string
  readonly cores: number
  readonly memoryMib: number
  readonly diskMib: number
  readonly sandboxDiskMib: number
  readonly maxSandboxes: number
  readonly defaultSize?: MacResources
}

/** The price of reserving one Mac for a period. */
export interface MacPrice {
  readonly centsPerHour: number
  readonly periodCents: number
  readonly periodSeconds: number
  readonly currency: string
}

export interface MacCatalogModel {
  readonly model: MacModel
  /** Available Macs are advisory; another purchase can consume the capacity. */
  readonly available: number
  /** Absent when pricing is unavailable. */
  readonly price?: MacPrice
}

export interface MacCatalogRegion {
  readonly id: string
  readonly defaultModel: string
  readonly models: readonly MacCatalogModel[]
}

export interface MacCatalog {
  readonly regions: readonly MacCatalogRegion[]
}

export interface MacCatalogOptions {
  readonly region?: string
}

export type MacReservationStatus = (typeof STATUSES)[number] | 'unknown'
export type MacReservationEndReason = (typeof END_REASONS)[number] | 'unknown'
/** How the reservation was bought: explicitly or by auto-reserve. */
export type MacReservationOrigin = (typeof ORIGINS)[number] | 'unknown'

/** A dedicated-Mac reservation. Timestamps are RFC 3339 strings. */
export interface MacReservation {
  readonly id: string
  readonly orgId: string
  readonly projectId: string
  readonly region: string
  readonly autoRenew: boolean
  readonly sandboxesPerMac: number
  readonly slotsUsed: number
  readonly model?: string
  readonly capacity?: MacResources
  readonly resourcesUsed: MacResources
  readonly periodN: number
  readonly periodSeconds: number
  readonly createdAt: string
  readonly activatedAt?: string
  readonly pendingMacStartedAt?: string
  readonly periodStartedAt?: string
  readonly periodEnd?: string
  readonly reissuedAt?: string
  readonly endedAt?: string
  readonly renewalPending: boolean
  readonly renewalError?: string
  readonly status: MacReservationStatus
  readonly rawStatus: string
  readonly endReason?: MacReservationEndReason
  readonly rawEndReason?: string
  readonly origin: MacReservationOrigin
  readonly rawOrigin: string
}

/** Reservations created by one bulk purchase. */
export interface MacReservationBatch {
  readonly requested: number
  readonly reserved: number
  readonly reservations: readonly MacReservation[]
}

export interface ListReservationsOptions {
  readonly cursor?: string
  readonly limit?: number
}

export interface CreateReservationOptions {
  /** Region slug to reserve a Mac in. */
  readonly region: string
  /** Omitted uses the region's default model; unavailable models are not upgraded. */
  readonly model?: string
  /** Reuse this key on retries to avoid reserving and charging for a second Mac. */
  readonly idempotencyKey?: string
  /** Omitted uses the organization's auto-reserve setting at purchase time. */
  readonly autoRenew?: boolean
}

export interface CreateReservationsBulkOptions extends CreateReservationOptions {
  /** How many Macs to reserve, 1 to 100. */
  readonly count: number
  /** The fewest Macs to accept, 1 to count. Omitted means all or nothing. */
  readonly minCount?: number
}

export interface RenewReservationOptions {
  /** Reuse this key on retries to avoid charging for another period. */
  readonly idempotencyKey?: string
}

export interface UpdateReservationOptions {
  readonly autoRenew: boolean
}
