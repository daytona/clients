/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import './fake-sdk'
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { Daytona } from '@daytona/sdk'
import { ImageBuilder } from '@daytona/sdk/v2'

test('build-based fake sandboxes preserve environment and attach secret placeholders', async () => {
  // Given an environment override and an existing project secret.
  const client = new Daytona({ apiKey: 'fixture' }).v2
  await client.secrets.set('BUILD_SECRET', 'hidden', { allowedDomains: ['example.com'] })
  // When creating from a build with both kinds of configuration.
  const sandbox = await client.createFromBuild(ImageBuilder.fromRegistry('base'), { env: { APP_ENV: 'override' }, secrets: ['BUILD_SECRET'] })
  // Then processes see the override and only the placeholder, never the secret.
  assert.equal((await sandbox.process.run('printf %s "$APP_ENV"')).output, 'override')
  assert.equal((await sandbox.process.run('printf %s "$BUILD_SECRET"')).output, 'DAYTONA_SECRET_PLACEHOLDER')
  await sandbox.delete()
  await client.secrets.delete('BUILD_SECRET')
})

test('build-based fake sandboxes refuse secrets the project lacks', async () => {
  // Given a project without the requested secret.
  const client = new Daytona({ apiKey: 'fixture' }).v2
  // When creating a build-based sandbox attaching it.
  const pending = client.createFromBuild(ImageBuilder.fromRegistry('base'), { secrets: ['MISSING_BUILD_SECRET'] })
  // Then the fake rejects the create rather than silently omitting the secret.
  await assert.rejects(pending)
})
