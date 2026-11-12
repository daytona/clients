/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { sameBuildLogResource } from '../src/v2/buildLogIdentity.js'

// Mirrors sdk-python/tests/test_build_logs.py: both SDKs accept the same
// region grammar (1-64 ASCII [A-Za-z0-9_-]) before attaching credentials.
describe('sameBuildLogResource region grammar', () => {
  const BODY = '3FP9SaFP7qfRTj4GM9xtju'
  const UUID = '00000000-0000-0000-0000-000000000000'

  it.each(['r', 'r'.repeat(64), 'region_a'])('accepts region %p', region => {
    expect(sameBuildLogResource('sandboxes', BODY, `${BODY}-${region}`)).toBe(true)
  })

  it.each(['', 'region.a', 'region a', 'r'.repeat(65), 'é'])('rejects region %p', region => {
    expect(sameBuildLogResource('sandboxes', BODY, `${BODY}-${region}`)).toBe(false)
    expect(sameBuildLogResource('sandboxes', `${BODY}-${region}`, BODY)).toBe(false)
  })

  it('requires matching tags only when both ids carry one', () => {
    expect(sameBuildLogResource('sandboxes', `${BODY}-region-a`, `${BODY}-region-b`)).toBe(false)
    expect(sameBuildLogResource('sandboxes', `${BODY}-region-a`, `${BODY}-region-a`)).toBe(true)
  })

  it('compares image UUIDs by identity, never base58', () => {
    expect(sameBuildLogResource('images', UUID.toUpperCase(), UUID)).toBe(true)
    expect(sameBuildLogResource('images', '1111111111111111', UUID)).toBe(false)
  })
})
