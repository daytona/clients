/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import axios from 'axios'
import type * as api from '../src/v2/gen/api-types.js'
import { ReservationsService } from '../src/v2/Reservations.js'
import { INTERNAL } from '../src/v2/internal'
import type { MacReservation } from '../src/v2/macTypes.js'

// Full API response fixture, including optional lifecycle and billing fields.
const wire = {
  id: 'region-a:reservation-1',
  org_id: '00000000-0000-0000-0000-000000000001',
  project_id: '00000000-0000-0000-0000-000000000002',
  region_id: 'region-a',
  status: 'released',
  origin: 'auto',
  auto_renew: false,
  slots_used: 2,
  model: 'm4-10c-16g',
  capacity: { cores: 10, memory_mib: 16384, disk_mib: 235520 },
  resources_used: { cores: 5, memory_mib: 8192, disk_mib: 117760 },
  period_n: 3,
  period_seconds: 86400,
  created_at: '2026-10-20T00:00:00Z',
  pending_mac_started_at: '2026-10-20T00:01:00Z',
  activated_at: '2026-10-20T00:02:00Z',
  period_started_at: '2026-10-22T00:02:00Z',
  period_end: '2026-10-23T00:02:00Z',
  reissued_at: '2026-10-21T00:00:00Z',
  ended_at: '2026-10-23T00:02:01Z',
  end_reason: 'expired',
  renewal_pending: true,
  renewal_error: 'budget held',
} satisfies Required<api.MacReservation>

const expected = {
  id: wire.id, orgId: wire.org_id, projectId: wire.project_id, region: wire.region_id,
  status: 'released', rawStatus: 'released', origin: 'auto', rawOrigin: 'auto',
  autoRenew: false, slotsUsed: 2, model: wire.model,
  capacity: { cores: 10, memoryMib: 16384, diskMib: 235520 },
  resourcesUsed: { cores: 5, memoryMib: 8192, diskMib: 117760 },
  periodN: 3, periodSeconds: 86400, createdAt: wire.created_at,
  pendingMacStartedAt: wire.pending_mac_started_at, activatedAt: wire.activated_at,
  periodStartedAt: wire.period_started_at, periodEnd: wire.period_end,
  reissuedAt: wire.reissued_at, endedAt: wire.ended_at,
  endReason: 'expired', rawEndReason: 'expired', renewalPending: true, renewalError: 'budget held',
} satisfies MacReservation

const operations = [
  ['get', (service: ReservationsService) => service.get(wire.id)],
  ['create', (service: ReservationsService) => service.create({ region: wire.region_id })],
  ['renew', (service: ReservationsService) => service.renew(wire.id)],
  ['update', (service: ReservationsService) => service.update(wire.id, { autoRenew: false })],
  ['list', async (service: ReservationsService) => (await service.list()).items[0]],
  ['createBulk', async (service: ReservationsService) => (await service.createBulk({ region: wire.region_id, count: 1 })).reservations[0]],
] as const

describe('reservation wire-to-public fields', () => {
  it('maps nested resources when the API returns physical capacity and usage', async () => {
    // Given
    const http = axios.create({ adapter: async (config) => ({
      config, status: 200, statusText: 'OK', headers: {}, data: wire,
    }) })
    // When
    const result = await new ReservationsService(INTERNAL, http).get(wire.id)
    // Then: exact shapes also exclude the renamed snake_case keys.
    expect(result.capacity).toEqual({ cores: 10, memoryMib: 16384, diskMib: 235520 })
    expect(result.resourcesUsed).toEqual({ cores: 5, memoryMib: 8192, diskMib: 117760 })
  })

  it('ignores unknown top-level fields when the API adds reservation metadata', async () => {
    // Given: future fields include a nested object and snake_case names.
    const futureFields = { future_metadata: { revision: 2 }, futureFlag: true }
    const http = axios.create({ adapter: async (config) => ({
      config, status: 200, statusText: 'OK', headers: {}, data: { ...wire, ...futureFields },
    }) })
    // When
    const result = await new ReservationsService(INTERNAL, http).get(wire.id)
    // Then: only the SDK-owned model is returned.
    expect(result).toEqual(expected)
  })

  it.each(operations)('maps every API field when %s returns a reservation', async (_name, operation) => {
    // Given: actual wire names, with envelopes for the two collection routes.
    const http = axios.create({ adapter: async (config) => ({
      config, status: 200, statusText: 'OK', headers: {},
      data: config.url === '/reservations/bulk'
        ? { requested: 1, reserved: 1, reservations: [wire] }
        : config.method === 'get' && config.url === '/reservations'
          ? { reservations: [wire], next_cursor: null } : wire,
    }) })
    // When
    const result = await operation(new ReservationsService(INTERNAL, http))
    // Then: known renamed snake_case fields are absent from the public object.
    expect(result).toEqual(expected)
  })

  it('exposes only SDK batch fields when a bulk purchase includes future metadata', async () => {
    // Given
    const http = axios.create({ adapter: async (config) => ({
      config, status: 200, statusText: 'OK', headers: {},
      data: { requested: 2, reserved: 1, reservations: [wire], future_metadata: true },
    }) })
    // When
    const result = await new ReservationsService(INTERNAL, http).createBulk({ region: wire.region_id, count: 2, minCount: 1 })
    // Then
    expect(result).toEqual({ requested: 2, reserved: 1, reservations: [expected] })
  })

  it('preserves raw values when reservation enums are unknown', async () => {
    // Given
    const http = axios.create({ adapter: async (config) => ({
      config, status: 200, statusText: 'OK', headers: {},
      data: { ...wire, status: 'future_status', origin: 'future_origin', end_reason: 'future_reason' },
    }) })
    // When
    const result = await new ReservationsService(INTERNAL, http).get(wire.id)
    // Then
    expect(result).toEqual({ ...expected, status: 'unknown', rawStatus: 'future_status',
      origin: 'unknown', rawOrigin: 'future_origin', endReason: 'unknown', rawEndReason: 'future_reason' })
  })

  it.each([null, undefined])('normalizes optional fields when the wire value is %s', async (value) => {
    // Given: a reservation that has not acquired a Mac or started its period.
    const http = axios.create({ adapter: async (config) => ({
      config, status: 200, statusText: 'OK', headers: {},
      data: JSON.stringify({ ...wire, auto_renew: true, slots_used: 0,
        activated_at: value, capacity: value, model: value, end_reason: value,
        ended_at: value, pending_mac_started_at: value, period_end: value,
        period_started_at: value, reissued_at: value, renewal_error: value }),
    }) })
    // When
    const result = await new ReservationsService(INTERNAL, http).get(wire.id)
    // Then
    expect(result).toEqual({ ...expected, autoRenew: true, slotsUsed: 0,
      activatedAt: undefined, capacity: undefined, model: undefined, endReason: undefined, rawEndReason: undefined,
      endedAt: undefined, pendingMacStartedAt: undefined, periodEnd: undefined,
      periodStartedAt: undefined, reissuedAt: undefined, renewalError: undefined })
  })
})

// The contract's date-time fields are RFC 3339 timestamps; a date, or one
// without an offset, is refused naming the field (as in Python).
describe.each([
  'created_at', 'activated_at', 'pending_mac_started_at', 'period_started_at', 'period_end', 'reissued_at', 'ended_at',
])('a reservation %s', (field) => {
  it.each(['nope', '2026-10-20', '2026-10-20T00:00:00'])('that is %j is refused', async (stamp) => {
    const http = axios.create({ adapter: async (config) => ({
      config, status: 200, statusText: 'OK', headers: {}, data: { ...wire, [field]: stamp },
    }) })
    await expect(new ReservationsService(INTERNAL, http).get(wire.id)).rejects.toMatchObject({
      statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringContaining(`has a ${field} that is not an RFC 3339 timestamp`),
    })
  })
})
