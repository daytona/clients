/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { AxiosInstance } from 'axios'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import type * as api from './gen/api-types.js'
import type { MacCatalog, MacModel } from './types.js'

/**
 * The Mac model registry (`GET /mac-models`): the models `reservations.create({ model })`
 * accepts, with each model's CPU cores, physical memory, physical SSD capacity and
 * sandbox disk room in MiB, maximum sandbox count, and default sandbox size. `catalog()` adds free Macs and prices.
 */
export class MacModelsService {
  constructor(private readonly http: AxiosInstance) {
    credentialSafeDiagnostics(this, () => ({ type: 'MacModelsService' }))
  }

  /** Every registered Mac model, ordered by `id`. */
  public async list(): Promise<MacModel[]> {
    const { data } = await this.http.get<api.MacModelList>('/mac-models')
    return data.mac_models
  }

  /** Free Macs and prices per Mac model (GET /mac-catalog). available is advisory; price is null when pricing is unavailable. */
  public async catalog(options: { region?: string } = {}): Promise<MacCatalog> {
    const { data } = await this.http.get<MacCatalog>('/mac-catalog', options.region === undefined ? undefined : { params: { region_ids: options.region } })
    return data
  }
}
