/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { listPage } from './listPage.js'
import type { AxiosInstance } from 'axios'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import { assertInternal } from './internal.js'
import type { Internal } from './internal.js'
import type * as api from './gen/api-types.js'
import type { MacCatalog, MacCatalogOptions, MacModel } from './macTypes.js'
import { mapMacModel } from './macMappers.js'
import { assertKnownOptions, optionKeys } from './options.js'

const MAC_CATALOG_KEYS = optionKeys<MacCatalogOptions>()({ region: true })

/**
 * The Mac model registry (`GET /mac-models`): the models `reservations.create({ model })`
 * accepts, with each model's CPU cores, physical memory, physical SSD capacity and
 * sandbox disk room in MiB, maximum sandbox count, and default sandbox size. `catalog()` adds free Macs and prices.
 */
export class MacModelsService {
  /** @internal */
  constructor(token: Internal, http: AxiosInstance)
  /** Built by the SDK: get it from the client or a sandbox. */
  constructor(token: Internal, init: never)
  constructor(token: Internal, private readonly http: AxiosInstance) {
    assertInternal(token, 'MacModelsService')
    credentialSafeDiagnostics(this, () => ({ type: 'MacModelsService' }))
  }

  /** Every registered Mac model, ordered by `id`. */
  public async list(): Promise<readonly MacModel[]> {
    const { data } = await this.http.get<api.MacModelList>('/mac-models')
    // The registry is not paged: `mac_models` is its whole envelope.
    return listPage<api.MacModel>(data, 'mac_models', 'Mac model list').items.map(mapMacModel)
  }

  /** Free Macs and prices per model. Availability is advisory; price is absent when unavailable. */
  public async catalog(options: MacCatalogOptions = {}): Promise<MacCatalog> {
    assertKnownOptions('MacCatalogOptions', options, MAC_CATALOG_KEYS)
    const { data } = await this.http.get<api.MacCatalog>('/mac-catalog', options.region === undefined ? undefined : { params: { region_ids: options.region } })
    return {
      regions: data.regions.map((region) => ({
        id: region.id,
        defaultModel: region.default_model,
        models: region.models.map((entry) => ({
          model: mapMacModel(entry.model),
          available: entry.available,
          price: entry.price == null ? undefined : {
            centsPerHour: entry.price.cents_per_hour,
            periodCents: entry.price.period_cents,
            periodSeconds: entry.price.period_seconds,
            currency: entry.price.currency,
          },
        })),
      })),
    }
  }
}
