/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import axios from 'axios'
import { MacModelsService } from '../src/v2/MacModels.js'
import { INTERNAL } from '../src/v2/internal'

const model = { id: 'm4', name: 'Mac M4', cores: 10, memory_mib: 16384, disk_mib: 245760,
  sandbox_disk_mib: 204800, max_sandboxes: 2, default_size: { cores: 5, memory_mib: 8192, disk_mib: 102400 } }
const expected = { id: 'm4', name: 'Mac M4', cores: 10, memoryMib: 16384, diskMib: 245760,
  sandboxDiskMib: 204800, maxSandboxes: 2, defaultSize: { cores: 5, memoryMib: 8192, diskMib: 102400 } }

describe('Mac model registry', () => {
  it('maps nested default resources when listing registered models', async () => {
    // Given
    const http = axios.create({ adapter: async (config) => ({ config, status: 200, statusText: 'OK', headers: {},
      data: { mac_models: [{ ...model, future_model_field: true }] } }) })
    // When
    const result = await new MacModelsService(INTERNAL, http).list()
    // Then
    expect(result).toEqual([expected])
  })

  it('maps catalog pricing and omits nullable model defaults and unavailable prices', async () => {
    // Given
    const params: unknown[] = []
    const http = axios.create({ adapter: async (config) => {
      params.push(config.params)
      return { config, status: 200, statusText: 'OK', headers: {}, data: {
        regions: [{ id: 'region-a', default_model: 'm4', future_region_field: true, models: [
          { model, available: 3, price: { cents_per_hour: 135.12, period_cents: 3243, period_seconds: 86400, currency: 'usd', future_price_field: true } },
          { model: { ...model, default_size: null }, available: 0, price: null },
        ] }],
      } }
    } })
    // When
    const result = await new MacModelsService(INTERNAL, http).catalog({ region: 'region-a' })
    // Then
    expect(params).toEqual([{ region_ids: 'region-a' }])
    expect(result).toEqual({ regions: [{ id: 'region-a', defaultModel: 'm4', models: [
      { model: expected, available: 3, price: { centsPerHour: 135.12, periodCents: 3243, periodSeconds: 86400, currency: 'usd' } },
      { model: { ...expected, defaultSize: undefined }, available: 0, price: undefined },
    ] }] })
  })
})
