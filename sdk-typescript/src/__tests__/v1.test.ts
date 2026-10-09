// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import * as root from '../index'
import * as v1 from '../v1'
import { Daytona as V1Daytona } from '../v1'

describe('@daytona/sdk/v1', () => {
  it('re-exports every runtime export of the root entry point', () => {
    expect(Object.keys(v1).sort()).toEqual(Object.keys(root).sort())
    for (const name of Object.keys(root) as (keyof typeof root)[]) {
      expect(v1[name]).toBe(root[name])
    }
  })

  it('exposes the same Daytona class', () => {
    expect(V1Daytona).toBe(root.Daytona)
  })
})
