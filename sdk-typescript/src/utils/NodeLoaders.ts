/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Loaders for the Node-only modules that local-file, stream and object storage helpers use.
 *
 * Every module is a static import, a literal import() or createRequire(import.meta.url) with a
 * literal specifier, the forms bundlers and server output tracing follow. In the ESM build,
 * post-build.js publishes this file behind the `#node-loaders` import: the "node" condition
 * gets this file, and every other condition (browsers, edge, workers) gets an empty stub, so
 * those bundles never reference a Node module. Keep every specifier a string literal.
 * OpenTelemetry is loaded by UntracedRequire instead.
 */

import * as toml from '@iarna/toml'
import * as buffer from 'buffer'
import busboy from 'busboy'
import expandTilde from 'expand-tilde'
import * as fs from 'fs'
import { createRequire } from 'module'
import * as stream from 'stream'
import * as util from 'util'

// tinyglobby is not imported statically: its ESM entry calls createRequire(import.meta.url)
// while loading, which throws when an app re-bundles this ESM build to CommonJS and would
// break importing the SDK. It is required synchronously instead (post-build.js turns
// __filename into import.meta.url in ESM, a form Node, webpack and Turbopack resolve), and
// imported ahead of use for bundlers that leave that require to runtime (Next.js webpack).
let tinyglobby: unknown
import('tinyglobby').then(
  (mod) => {
    tinyglobby = mod
  },
  () => undefined,
)

/** Thrown by a loader that could not run, so the caller uses its fallback. */
class NodeLoaderNotReadyError extends Error {
  readonly code = 'ERR_DAYTONA_NODE_LOADER_NOT_READY'
}

function loadTinyglobby(): unknown {
  if (tinyglobby !== undefined) return tinyglobby
  try {
    tinyglobby = createRequire(__filename)('tinyglobby')
    return tinyglobby
  } catch (error) {
    throw new NodeLoaderNotReadyError(`tinyglobby could not be loaded yet: ${(error as Error)?.message ?? error}`)
  }
}

/** Synchronous loaders, used by dynamicRequire and dynamicImport. */
export const nodeLoaders: Partial<Record<string, () => unknown>> = {
  fs: () => fs,
  stream: () => stream,
  buffer: () => buffer,
  util: () => util,
  tinyglobby: loadTinyglobby,
  'expand-tilde': () => expandTilde,
  '@iarna/toml': () => toml,
  busboy: () => busboy,
}

/** Asynchronous loaders, used by dynamicImport only. */
export const nodeImporters: Partial<Record<string, () => Promise<unknown>>> = {
  tar: () => import('tar'),
  'form-data': () => import('form-data').then((mod) => mod.default),
  ObjectStorage: () => import('../ObjectStorage.js'),
}
