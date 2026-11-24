/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Loaders for the Node-only modules that local-file, stream, object storage and
 * OpenTelemetry helpers use.
 *
 * Each loader requires (or, for SDK modules, imports) a string literal so bundlers and
 * server output tracing see the dependency. In the ESM build, post-build.js publishes this
 * file behind the `#node-loaders` import: under the "node" condition each require goes
 * through `createRequire(import.meta.url)`, and every other condition (browsers, edge,
 * workers) gets an empty stub, so those bundles never reference a Node module.
 * Keep every specifier a string literal, and keep this file free of other code.
 */

export const nodeLoaders: Partial<Record<string, () => unknown>> = {
  fs: () => require('fs'),
  stream: () => require('stream'),
  buffer: () => require('buffer'),
  util: () => require('util'),
  tinyglobby: () => require('tinyglobby'),
  'expand-tilde': () => require('expand-tilde'),
  '@iarna/toml': () => require('@iarna/toml'),
  tar: () => require('tar'),
  'form-data': () => require('form-data'),
  busboy: () => require('busboy'),
  '@opentelemetry/api': () => require('@opentelemetry/api'),
  '@opentelemetry/sdk-node': () => require('@opentelemetry/sdk-node'),
  '@opentelemetry/instrumentation-http': () => require('@opentelemetry/instrumentation-http'),
  '@opentelemetry/sdk-trace': () => require('@opentelemetry/sdk-trace'),
  '@opentelemetry/exporter-trace-otlp-http': () => require('@opentelemetry/exporter-trace-otlp-http'),
  '@opentelemetry/otlp-exporter-base': () => require('@opentelemetry/otlp-exporter-base'),
  '@opentelemetry/semantic-conventions': () => require('@opentelemetry/semantic-conventions'),
  '@opentelemetry/resources': () => require('@opentelemetry/resources'),
  ObjectStorage: () => import('../ObjectStorage.js'),
}
