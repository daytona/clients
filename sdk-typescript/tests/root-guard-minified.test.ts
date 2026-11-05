/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Daytona } from '../src/Daytona'

describe('moved-member guidance for bundled clients', () => {
  it('falls back to Daytona when the class name is minified', () => {
    // Given: a client subclass with a typical minified name.
    class t extends Daytona {}
    const root = new t({ apiKey: 'key' })

    // When / Then: reading a moved member names the public client, not the bundle symbol.
    expect(() => Reflect.get(root, 'create')).toThrow(
      'SDK 2.x moved Daytona.create to Daytona.v0.create',
    )
  })

  it('preserves a meaningful three-character subclass name', () => {
    // Given: a client subclass at the meaningful-name length boundary.
    class App extends Daytona {}
    const root = new App({ apiKey: 'key' })

    // When / Then: reading a moved member keeps the subclass identity.
    expect(() => Reflect.get(root, 'create')).toThrow(
      'SDK 2.x moved App.create to App.v0.create',
    )
  })
})
