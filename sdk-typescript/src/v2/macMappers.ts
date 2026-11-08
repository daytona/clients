/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type * as api from './gen/api-types.js'
import type { MacModel, MacResources } from './macTypes.js'

/** @internal */
export function mapMacResources(resources: NonNullable<api.MacModel['default_size']>): MacResources {
  return { cores: resources.cores, memoryMib: resources.memory_mib, diskMib: resources.disk_mib }
}

/** @internal */
export function mapMacModel(model: api.MacModel): MacModel {
  return {
    id: model.id,
    name: model.name,
    cores: model.cores,
    memoryMib: model.memory_mib,
    diskMib: model.disk_mib,
    sandboxDiskMib: model.sandbox_disk_mib,
    maxSandboxes: model.max_sandboxes,
    defaultSize: model.default_size == null ? undefined : mapMacResources(model.default_size),
  }
}
