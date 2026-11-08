/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { headerText, pageParams } from './pageParams.js'
import { pathSegment } from './pathSegment.js'
import { deepFrozen, frozenPage } from './frozen.js'
import { listPage } from './listPage.js'
import type { AxiosInstance } from 'axios'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import { assertInternal } from './internal.js'
import type { Internal } from './internal.js'
import type { CreateReservationOptions, CreateReservationsBulkOptions, ListReservationsOptions, MacReservation, MacReservationBatch, RenewReservationOptions, UpdateReservationOptions } from './macTypes.js'
import type { ListResult } from './types.js'
import type * as api from './gen/api-types.js'
import { runtimeUniqueId } from './http.js'
import { MacReservationEndReason as END_REASONS, MacReservationOrigin as ORIGINS, MacReservationStatus as STATUSES } from './gen/api-enums.js'
import { knownOrUnknown } from './knownValue.js'
import { mapMacResources } from './macMappers.js'
import { assertKnownOptions, assertOptionsObject, badRequest, optionKeys, snapshotOptions } from './options.js'
import { arrayOf, checkResponse, MAC_RESERVATION, nested } from './responseFields.js'

const CREATE_RESERVATION_KEYS = optionKeys<CreateReservationOptions>()({ region: true, model: true, autoRenew: true, idempotencyKey: true })
const CREATE_RESERVATIONS_BULK_KEYS = optionKeys<CreateReservationsBulkOptions>()({ region: true, model: true, autoRenew: true, idempotencyKey: true, count: true, minCount: true })
const LIST_RESERVATIONS_KEYS = optionKeys<ListReservationsOptions>()({ cursor: true, limit: true })
const RENEW_RESERVATION_KEYS = optionKeys<RenewReservationOptions>()({ idempotencyKey: true })
const UPDATE_RESERVATION_KEYS = optionKeys<UpdateReservationOptions>()({ autoRenew: true })

/** Every reservation option is checked before any request, as in Python. */
function checkReservationOptions(options: { readonly region?: unknown; readonly model?: unknown; readonly autoRenew?: unknown }): void {
  // A blank region names no region and a blank model is the region's default
  // model (the contract), so only a non-string is refused.
  if (typeof options.region !== 'string') throw badRequest('region must be a string')
  if (options.model !== undefined && typeof options.model !== 'string') throw badRequest('model must be a string')
  if (options.autoRenew !== undefined && typeof options.autoRenew !== 'boolean') throw badRequest('autoRenew must be a boolean')
}

function reservation(wire: api.MacReservation): MacReservation {
  const value = checkResponse<api.MacReservation>(wire, MAC_RESERVATION, 'reservation')
  return deepFrozen({
    id: value.id,
    region: value.region_id,
    orgId: value.org_id,
    projectId: value.project_id,
    autoRenew: value.auto_renew,
    sandboxesPerMac: value.sandboxes_per_mac,
    slotsUsed: value.slots_used,
    model: value.model ?? undefined,
    capacity: value.capacity == null ? undefined : mapMacResources(value.capacity),
    resourcesUsed: mapMacResources(value.resources_used),
    periodN: value.period_n,
    periodSeconds: value.period_seconds,
    createdAt: value.created_at,
    activatedAt: value.activated_at ?? undefined,
    pendingMacStartedAt: value.pending_mac_started_at ?? undefined,
    periodStartedAt: value.period_started_at ?? undefined,
    periodEnd: value.period_end ?? undefined,
    reissuedAt: value.reissued_at ?? undefined,
    endedAt: value.ended_at ?? undefined,
    renewalPending: value.renewal_pending,
    renewalError: value.renewal_error ?? undefined,
    status: knownOrUnknown(value.status, STATUSES),
    rawStatus: value.status,
    endReason: value.end_reason == null ? undefined : knownOrUnknown(value.end_reason, END_REASONS),
    rawEndReason: value.end_reason ?? undefined,
    origin: knownOrUnknown(value.origin, ORIGINS),
    rawOrigin: value.origin,
  })
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
  /** @internal */
  constructor(token: Internal, http: AxiosInstance)
  /** Built by the SDK: get it from the client or a sandbox. */
  constructor(token: Internal, init: never)
  constructor(token: Internal, private readonly http: AxiosInstance) {
    assertInternal(token, 'ReservationsService')
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
    assertOptionsObject('CreateReservationOptions', options)
    assertKnownOptions('CreateReservationOptions', options, CREATE_RESERVATION_KEYS)
    checkReservationOptions(options)
    const body: api.CreateReservationRequest = { region_id: options.region }
    if (options.model !== undefined) body.model = options.model
    if (options.autoRenew !== undefined) body.auto_renew = options.autoRenew
    const { data } = await this.http.post<api.MacReservation>('/reservations', body, idempotencyHeaders(options.idempotencyKey))
    return reservation(data)
  }

  /**
   * Reserve count Macs of one model in region in one request. minCount (default count) is the fewest to accept: with fewer Macs free, the request reserves as many as are free, or none, failing with ConflictError code === 'NO_MAC_AVAILABLE' when fewer than minCount are free. The request always carries an Idempotency-Key: idempotencyKey when given, otherwise a random key the SDK generates for this call. Pass your own key if you may retry, so a retry after a lost response returns the same reservations instead of reserving more Macs.
   */
  public async createBulk(options: CreateReservationsBulkOptions): Promise<MacReservationBatch> {
    assertOptionsObject('CreateReservationsBulkOptions', options)
    assertKnownOptions('CreateReservationsBulkOptions', options, CREATE_RESERVATIONS_BULK_KEYS)
    checkReservationOptions(options)
    // Billed: the counts the contract allows only (count 1 to 100, minCount 1 to count).
    const count: unknown = options.count
    if (typeof count !== 'number' || !Number.isInteger(count) || count < 1 || count > 100) throw badRequest('count must be an integer from 1 to 100')
    const minCount: unknown = options.minCount
    if (minCount !== undefined && (typeof minCount !== 'number' || !Number.isInteger(minCount) || minCount < 1 || minCount > count)) {
      throw badRequest('minCount must be an integer from 1 to count')
    }
    const body: api.CreateReservationsBulkRequest = { region_id: options.region, count: options.count }
    if (options.minCount !== undefined) body.min_count = options.minCount
    if (options.model !== undefined) body.model = options.model
    if (options.autoRenew !== undefined) body.auto_renew = options.autoRenew
    const { data } = await this.http.post<api.MacReservationBatch>('/reservations/bulk', body, idempotencyHeaders(options.idempotencyKey))
    const batch = checkResponse<api.MacReservationBatch>(data, { requested: 'integer', reserved: 'integer', reservations: arrayOf(nested(MAC_RESERVATION)) }, 'reservation batch')
    return deepFrozen({ requested: batch.requested, reserved: batch.reserved, reservations: batch.reservations.map(reservation) })
  }

  /** One page of the organization's reservations. */
  public async list(options: ListReservationsOptions = {}): Promise<ListResult<MacReservation>> {
    options = snapshotOptions('ListReservationsOptions', options, LIST_RESERVATIONS_KEYS) ?? {}
    const { data } = await this.http.get<api.ListReservationsResponse>('/reservations', { params: pageParams(options.cursor, options.limit) })
    const page = listPage<api.MacReservation>(data, 'reservations', 'reservation list')
    return frozenPage({ items: page.items.map(reservation), nextCursor: page.nextCursor })
  }

  public async *iter(options: ListReservationsOptions = {}): AsyncIterable<MacReservation> {
    options = snapshotOptions('ListReservationsOptions', options, LIST_RESERVATIONS_KEYS) ?? {}
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
    const { data } = await this.http.get<api.MacReservation>(`/reservations/${pathSegment(id, 'id')}`)
    return reservation(data)
  }

  /**
   * Renew the reservation for its next period, which the server charges. The request always carries an Idempotency-Key: idempotencyKey when given, otherwise a random key the SDK generates for this call. Pass your own key if you may retry, so a retry after a lost response does not pay for a second period.
   */
  public async renew(id: string, options: RenewReservationOptions = {}): Promise<MacReservation> {
    assertKnownOptions('RenewReservationOptions', options, RENEW_RESERVATION_KEYS)
    const { data } = await this.http.post<api.MacReservation>(`/reservations/${pathSegment(id, 'id')}/renew`, undefined, idempotencyHeaders(options.idempotencyKey))
    return reservation(data)
  }

  /**
   * Turn the reservation's auto-renewal on or off. Turning it on needs the
   * organization's macOS access enabled; turning it off works
   * without that access, so an organization can stop a reservation renewing. Fails with
   * `ConflictError` when the reservation is no longer live.
   */
  public async update(id: string, options: UpdateReservationOptions): Promise<MacReservation> {
    const path = `/reservations/${pathSegment(id, 'id')}`
    assertOptionsObject('UpdateReservationOptions', options)
    assertKnownOptions('UpdateReservationOptions', options, UPDATE_RESERVATION_KEYS)
    // The one field to change is required: no empty PATCH is sent.
    if (typeof options.autoRenew !== 'boolean') throw badRequest('autoRenew must be a boolean')
    const body: api.UpdateReservationRequest = { auto_renew: options.autoRenew }
    const { data } = await this.http.patch<api.MacReservation>(path, body)
    return reservation(data)
  }
}

// Every purchase and renewal carries a key: the caller's, or a fresh one per call.
function idempotencyHeaders(idempotencyKey: string | undefined) {
  if (idempotencyKey != null && typeof idempotencyKey !== 'string') {
    throw badRequest('idempotencyKey must be a string')
  }
  return { headers: { 'Idempotency-Key': idempotencyKey == null ? runtimeUniqueId() : headerText(idempotencyKey, 'idempotencyKey') } }
}
