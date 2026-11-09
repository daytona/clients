/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { deepFrozen } from './frozen.js'
import type * as api from './gen/api-types.js'
import type { MacModel, MacResources } from './macTypes.js'
import { checkResponse, MAC_MODEL, MAC_RESOURCES } from './responseFields.js'

/** @internal */
export function mapMacResources(value: NonNullable<api.MacModel['default_size']>): MacResources {
  const resources = checkResponse<NonNullable<api.MacModel['default_size']>>(value, MAC_RESOURCES, 'Mac resources')
  return deepFrozen({ cores: resources.cores, memoryMib: resources.memory_mib, diskMib: resources.disk_mib })
}

/** @internal */
export function mapMacModel(value: api.MacModel): MacModel {
  const model = checkResponse<api.MacModel>(value, MAC_MODEL, 'Mac model')
  return deepFrozen({
    id: model.id,
    name: model.name,
    cores: model.cores,
    memoryMib: model.memory_mib,
    diskMib: model.disk_mib,
    sandboxDiskMib: model.sandbox_disk_mib,
    maxSandboxes: model.max_sandboxes,
    defaultSize: model.default_size == null ? undefined : mapMacResources(model.default_size),
  })
}
