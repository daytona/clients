/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { AxiosInstance } from 'axios'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import type { CreateReservationOptions, CreateReservationRequest, CreateReservationsBulkOptions, CreateReservationsBulkRequest, ListReservationsOptions, ListResult, MacReservation, MacReservationBatch, RenewReservationOptions, UpdateReservationOptions, UpdateReservationRequest } from './types.js'
import type * as api from './gen/api-types.js'
import { runtimeUniqueId, strictUriEncode } from './http.js'
import { MacReservationEndReason as END_REASONS, MacReservationOrigin as ORIGINS, MacReservationStatus as STATUSES } from './gen/api-enums.js'
import { knownOrUnknown } from './knownValue.js'
import { badRequest } from './options.js'

function reservation(value: api.MacReservation): MacReservation {
  const {
    region_id: region, org_id: orgId, project_id: projectId, auto_renew: autoRenew,
    sandboxes_per_mac: sandboxesPerMac, slots_used: slotsUsed,
    resources_used: resourcesUsed, period_n: periodN, period_seconds: periodSeconds,
    created_at: createdAt, activated_at: activatedAt, pending_mac_started_at: pendingMacStartedAt,
    period_started_at: periodStartedAt, period_end: periodEnd, reissued_at: reissuedAt,
    ended_at: endedAt, renewal_pending: renewalPending, renewal_error: renewalError,
    end_reason: endReason, ...rest
  } = value
  return {
    ...rest,
    region, orgId, projectId, autoRenew, sandboxesPerMac, slotsUsed,
    capacity: value.capacity == null ? value.capacity : {
      cores: value.capacity.cores, memoryMib: value.capacity.memory_mib, diskMib: value.capacity.disk_mib,
    },
    resourcesUsed: {
      cores: resourcesUsed.cores, memoryMib: resourcesUsed.memory_mib, diskMib: resourcesUsed.disk_mib,
    },
    periodN, periodSeconds, createdAt, activatedAt, pendingMacStartedAt,
    periodStartedAt, periodEnd, reissuedAt, endedAt, renewalPending, renewalError,
    status: knownOrUnknown(value.status, STATUSES),
    rawStatus: value.status,
    endReason: endReason == null ? endReason : knownOrUnknown(endReason, END_REASONS),
    rawEndReason: endReason,
    origin: knownOrUnknown(value.origin, ORIGINS),
    rawOrigin: value.origin,
  }
}

/**
 * Dedicated-Mac reservations. A macOS sandbox runs on a Mac the organization
 * has reserved: reserve one here, or let a macOS create buy one
 * (`reservation: 'new'`, or the organization's auto-reserve). A reservation
 * lasts 24-hour periods, each charged when it starts. Reserving, renewing and
 * turning auto-renew on need macOS access enabled for the organization
 * (otherwise `403` `FEATURE_NOT_ENABLED`, thrown as
 * `FeatureNotEnabledError`); listing, reading and turning auto-renew off do not.
 */
export class ReservationsService {
  constructor(private readonly http: AxiosInstance) {
    credentialSafeDiagnostics(this, () => ({ type: 'ReservationsService' }))
  }

  /**
   * Reserve a Mac of `model` in `region`. Mac model slug from GET /mac-models. Omitted uses the region's default model. An unavailable model is never upgraded.
   * The request always carries an `Idempotency-Key`:
   * `idempotencyKey` when given, otherwise a random key the SDK generates for
   * this call. Pass your own key if you may retry, so a retry after a lost
   * response returns the same reservation instead of reserving a second Mac.
   * Fails with `ConflictError` `code === 'NO_MAC_AVAILABLE'` when no Mac of
   * that model is free in the region.
   */
  public async create(options: CreateReservationOptions): Promise<MacReservation> {
    const body: CreateReservationRequest = { region_id: options.region }
    if (options.model !== undefined) body.model = options.model
    if (options.autoRenew !== undefined) body.auto_renew = options.autoRenew
    const { data } = await this.http.post<api.MacReservation>('/reservations', body, idempotencyHeaders(options.idempotencyKey))
    return reservation(data)
  }

  /**
   * Reserve count Macs of one model in region in one request. minCount (default count) is the fewest to accept: with fewer Macs free, the request reserves as many as are free, or none, failing with ConflictError code === 'NO_MAC_AVAILABLE' when fewer than minCount are free. The request always carries an Idempotency-Key: idempotencyKey when given, otherwise a random key the SDK generates for this call. Pass your own key if you may retry, so a retry after a lost response returns the same reservations instead of reserving more Macs.
   */
  public async createBulk(options: CreateReservationsBulkOptions): Promise<MacReservationBatch> {
    const body: CreateReservationsBulkRequest = { region_id: options.region, count: options.count }
    if (options.minCount !== undefined) body.min_count = options.minCount
    if (options.model !== undefined) body.model = options.model
    if (options.autoRenew !== undefined) body.auto_renew = options.autoRenew
    const { data } = await this.http.post<api.MacReservationBatch>('/reservations/bulk', body, idempotencyHeaders(options.idempotencyKey))
    return { ...data, reservations: data.reservations.map(reservation) }
  }

  /** One page of the organization's reservations. */
  public async list(options: ListReservationsOptions = {}): Promise<ListResult<MacReservation>> {
    const { data } = await this.http.get<api.ListReservationsResponse>('/reservations', { params: options })
    return { items: data.reservations.map(reservation), nextCursor: data.next_cursor ?? undefined }
  }

  public async *iter(options: ListReservationsOptions = {}): AsyncIterable<MacReservation> {
    let cursor = options.cursor
    const seen = new Set<string>()
    if (cursor !== undefined) seen.add(cursor)
    for (;;) {
      const page = await this.list({ ...options, cursor })
      yield* page.items
      if (!page.nextCursor || page.nextCursor === cursor || seen.has(page.nextCursor)) return
      seen.add(page.nextCursor)
      cursor = page.nextCursor
    }
  }

  public async get(id: string): Promise<MacReservation> {
    const { data } = await this.http.get<api.MacReservation>(`/reservations/${strictUriEncode(id)}`)
    return reservation(data)
  }

  /**
   * Renew the reservation for its next period, which the server charges. The request always carries an Idempotency-Key: idempotencyKey when given, otherwise a random key the SDK generates for this call. Pass your own key if you may retry, so a retry after a lost response does not pay for a second period.
   */
  public async renew(id: string, options: RenewReservationOptions = {}): Promise<MacReservation> {
    const { data } = await this.http.post<api.MacReservation>(`/reservations/${strictUriEncode(id)}/renew`, undefined, idempotencyHeaders(options.idempotencyKey))
    return reservation(data)
  }

  /**
   * Turn the reservation's auto-renewal on or off. Turning it on needs the
   * organization's macOS access enabled; turning it off works
   * without that access, so an organization can stop a reservation renewing. Fails with
   * `ConflictError` when the reservation is no longer live.
   */
  public async update(id: string, options: UpdateReservationOptions): Promise<MacReservation> {
    const body: UpdateReservationRequest = { auto_renew: options.autoRenew }
    const { data } = await this.http.patch<api.MacReservation>(`/reservations/${strictUriEncode(id)}`, body)
    return reservation(data)
  }
}

// Every purchase and renewal carries a key: the caller's, or a fresh one per call.
function idempotencyHeaders(idempotencyKey: string | undefined) {
  if (idempotencyKey != null && typeof idempotencyKey !== 'string') {
    throw badRequest('idempotencyKey must be a string')
  }
  return { headers: { 'Idempotency-Key': idempotencyKey ?? runtimeUniqueId() } }
}
