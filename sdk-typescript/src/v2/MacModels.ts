/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { optionalQueryText } from './pageParams.js'
import { deepFrozen } from './frozen.js'
import type { AxiosInstance } from 'axios'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import { assertInternal } from './internal.js'
import type { Internal } from './internal.js'
import type * as api from './gen/api-types.js'
import type { MacCatalog, MacCatalogOptions, MacModel } from './macTypes.js'
import { mapMacModel } from './macMappers.js'
import { assertKnownOptions, badRequest, optionKeys } from './options.js'
import { arrayOf, checkResponse, MAC_MODEL, nested } from './responseFields.js'
import type { ResponseFields } from './responseFields.js'

const MAC_CATALOG_KEYS = optionKeys<MacCatalogOptions>()({ region: true })

const MAC_PRICE: ResponseFields = { cents_per_hour: 'number', period_cents: 'integer', currency: 'string', period_seconds: 'integer' }
const MAC_CATALOG: ResponseFields = {
  regions: arrayOf(nested({
    id: 'string', default_model: 'string',
    models: arrayOf(nested({ model: nested(MAC_MODEL), available: 'integer', price: nested(MAC_PRICE, 'nullable') })),
  })),
}

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
    // The registry is not paged: an object with a `mac_models` array of
    // objects, other fields ignored (a `next_cursor` there is unknown), as
    // Python's generated `MacModelList` reads it.
    if (typeof data !== 'object' || data === null || Array.isArray(data)) throw badRequest('Mac model list response is not a JSON object')
    const models: unknown = Reflect.get(data, 'mac_models')
    if (!Array.isArray(models)) throw badRequest('Mac model list response is missing the mac_models array')
    for (const model of models) {
      if (typeof model !== 'object' || model === null || Array.isArray(model)) throw badRequest('Mac model list response has a mac_models entry that is not an object')
    }
    // SAFETY: the envelope is checked above; mapMacModel checks each entry's fields.
    return Object.freeze((models as api.MacModel[]).map(mapMacModel))
  }

  /** Free Macs and prices per model. Availability is advisory; price is absent when unavailable. */
  public async catalog(options: MacCatalogOptions = {}): Promise<MacCatalog> {
    assertKnownOptions('MacCatalogOptions', options, MAC_CATALOG_KEYS)
    // One region string, checked as every query value is; null is omitted, as Python's None.
    const region = optionalQueryText(options.region, 'region')
    const response = await this.http.get<api.MacCatalog>('/mac-catalog', region === undefined ? undefined : { params: { region_ids: region } })
    const data = checkResponse<api.MacCatalog>(response.data, MAC_CATALOG, 'Mac catalog')
    return deepFrozen({
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
    })
  }
}
