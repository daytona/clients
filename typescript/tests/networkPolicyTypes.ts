/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type {
  CreateSandboxFromSnapshotOptions,
  CreateSandboxOptions,
} from '../src/v2/types'

type HasNetworkPolicy<T> = 'networkPolicy' extends keyof T ? true : false

const linuxCreateHasNetworkPolicy: HasNetworkPolicy<CreateSandboxOptions> = true
const linuxRestoreHasNetworkPolicy: HasNetworkPolicy<CreateSandboxFromSnapshotOptions> = false

void linuxCreateHasNetworkPolicy
void linuxRestoreHasNetworkPolicy
