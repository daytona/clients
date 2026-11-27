/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Loaders for the Node-only modules that local-file, stream and object storage helpers use.
 *
 * Every module is a static import or a literal import(), the forms bundlers and server
 * output tracing follow (Next.js webpack builds do not follow createRequire, and keep
 * import.meta.url pointing at the build machine). In the ESM build, post-build.js publishes
 * this file behind the `#node-loaders` import: the "node" condition gets this file, and every
 * other condition (browsers, edge, workers) gets an empty stub, so those bundles never
 * reference a Node module. Keep every specifier a string literal. OpenTelemetry is loaded by
 * UntracedRequire instead.
 */

import * as toml from '@iarna/toml'
import * as buffer from 'buffer'
import busboy from 'busboy'
import expandTilde from 'expand-tilde'
import * as fs from 'fs'
import * as stream from 'stream'
import * as util from 'util'

// tinyglobby is needed synchronously but is not imported statically: its ESM entry calls
// createRequire(import.meta.url) while loading, which throws when an app re-bundles this ESM
// build to CommonJS and would break importing the SDK. It is imported here ahead of use
// instead; until that settles, or if it fails, Import falls back to its own loader.
let tinyglobby: unknown
import('tinyglobby').then(
  (mod) => {
    tinyglobby = mod
  },
  () => undefined,
)

/** Thrown by a loader whose module is not ready, so the caller uses its fallback. */
class NodeLoaderNotReadyError extends Error {
  readonly code = 'ERR_DAYTONA_NODE_LOADER_NOT_READY'
}

export const nodeLoaders: Partial<Record<string, () => unknown>> = {
  fs: () => fs,
  stream: () => stream,
  buffer: () => buffer,
  util: () => util,
  tinyglobby: () => {
    if (tinyglobby === undefined) throw new NodeLoaderNotReadyError('tinyglobby has not been loaded yet')
    return tinyglobby
  },
  'expand-tilde': () => expandTilde,
  '@iarna/toml': () => toml,
  busboy: () => busboy,
  tar: () => import('tar'),
  'form-data': () => import('form-data').then((mod) => mod.default),
  ObjectStorage: () => import('../ObjectStorage.js'),
}
