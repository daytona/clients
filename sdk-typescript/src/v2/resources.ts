/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BadRequestError } from './errors/DaytonaError.js'

export function gibToMib(gib: number, field: string): number {
  const mib = gib * 1024
  if (typeof gib !== 'number' || !Number.isFinite(mib) || !Number.isInteger(mib)) {
    throw new BadRequestError(`${field} must be a GiB value that converts to a whole number of MiB`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return mib
}

export function mibToGib(mib: number | null | undefined): number | undefined {
  return mib === undefined || mib === null ? undefined : mib / 1024
}
