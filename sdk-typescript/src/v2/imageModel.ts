/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { ArtifactRetention, ImageOrigin, ImageSource, ImageStatus } from './types.js'

/** SDK-owned image metadata, independent of the wire DTO. */
export interface ImageModel {
  readonly id: string
  readonly name?: string
  readonly anonymous: boolean
  readonly availableIn: readonly string[]
  readonly bytes?: number
  readonly sandboxClass: string
  readonly createdAt: string
  readonly updatedAt: string
  readonly error?: string
  readonly expiresAt?: string
  readonly layerCount?: number
  readonly origin: ImageOrigin
  readonly region: string
  readonly retention: ArtifactRetention
  readonly rawRetention: string
  readonly source?: ImageSource
  readonly status: ImageStatus
  readonly formatVersion?: number
  readonly formatOutdated: boolean
  readonly platform: boolean
  readonly default: boolean
  readonly deprecated: boolean
  readonly diskMib?: number
}
