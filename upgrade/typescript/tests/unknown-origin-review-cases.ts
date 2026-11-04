/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { callbackIdentityReviewCases, callbackReviewCases, importedCallbackReviewCases, payloadReviewCases } from './payload-flow-cases.js';
import { receiverReviewCases } from './review-receiver-cases.js';

const sdk = "import { Daytona } from '@daytona/sdk';\n";
const fixme = { kind: 'fixme' } as const;
const unchanged = { kind: 'unchanged' } as const;

export const linkedReviewCases = [
  ...receiverReviewCases.filter(testCase => testCase.outcome.kind === 'fixme'),
  ...payloadReviewCases.filter(testCase => testCase.outcome.kind === 'fixme'),
  ...callbackReviewCases.filter(testCase => testCase.outcome.kind === 'fixme'),
  ...importedCallbackReviewCases,
  ...[false, true].flatMap(typed => [
    ...["Symbol('service')", "'service'"].map(key => ({
      name: `imported container key ${key} (typed=${typed})`, typed, path: 'use.js', outcome: fixme,
      source: "import { container, key as lookup } from './registry.js';\ncontainer.get(lookup).create();\n",
      files: { 'registry.js': `export const container = new Map(); export const key = ${key};`,
        'bootstrap.js': `${sdk}import { container, key as insertion } from './registry.js'; container.set(insertion, new Daytona());` },
    })),
    { name: `constructor supplied to local factory (typed=${typed})`, typed, path: 'use.js', outcome: fixme,
      source: `${sdk}const make = Cls => new Cls();\nconst client = make(Daytona);\nclient.create();\n` },
    { name: `constructor supplied to imported factory (typed=${typed})`, typed, path: 'use.js', outcome: fixme,
      source: `${sdk}import { make } from './factory.js';\nconst client = make(Daytona);\nclient.create();\n`,
      files: { 'factory.js': 'export const make = Cls => new Cls();' } },
    ...['daytona', 'client'].map(property => ({
      name: `call argument property ${property} (typed=${typed})`, typed, path: 'use.js', outcome: fixme,
      source: `export function handler(ctx) {\n  return ctx.${property}.create();\n}\n`,
      files: { 'bootstrap.js': `${sdk}import { handler } from './use.js'; handler({ ${property}: new Daytona() });` },
    })),
    { name: `bracket call argument property (typed=${typed})`, typed, path: 'use.js', outcome: fixme,
      source: "export function handler(ctx) {\n  return ctx['client'].create();\n}\n",
      files: { 'bootstrap.js': `${sdk}import { handler } from './use.js'; handler({ client: new Daytona() });` } },
  ]),
];

export const identityReviewCases = [
  ...callbackIdentityReviewCases,
  ...[false, true].flatMap(typed => [
    { name: `Daytona path namespace member (typed=${typed})`, typed, path: 'use.js', outcome: fixme,
      source: "import * as infra from './daytona/index.js';\ninfra.client.create();\n",
      files: { 'daytona/index.js': 'export const client = globalThis.opaque;' } },
    { name: `Daytona path namespace re-export (typed=${typed})`, typed, path: 'use.js', outcome: fixme,
      source: "import { services } from './barrel.js';\nservices.client.create();\n",
      files: { 'barrel.js': "export * as services from './daytona/index.js';",
        'daytona/index.js': 'export const client = globalThis.opaque;' } },
    { name: `nested Daytona namespace re-export (typed=${typed})`, typed, path: 'use.js', outcome: fixme,
      source: "import * as infra from './barrel.js';\ninfra.services.client.create();\n",
      files: { 'barrel.js': "export * as services from './daytona/index.js';",
        'daytona/index.js': 'export const client = globalThis.opaque;' } },
    { name: `aliased Daytona namespace re-export (typed=${typed})`, typed, path: 'use.js', outcome: fixme,
      source: "import { infra } from './alias.js';\ninfra.client.create();\n",
      files: { 'alias.js': "export { services as infra } from './barrel.js';",
        'barrel.js': "export * as services from './daytona/index.js';",
        'daytona/index.js': 'export const client = globalThis.opaque;' } },
  ]),
];

export const negativeReviewCases = [
  ...receiverReviewCases.filter(testCase => testCase.outcome.kind === 'unchanged'),
  ...payloadReviewCases.filter(testCase => testCase.outcome.kind === 'unchanged'),
  ...callbackReviewCases.filter(testCase => testCase.outcome.kind === 'unchanged'),
  ...[false, true].flatMap(typed => [
    { name: `different imported Symbol key (typed=${typed})`, typed: true, path: 'use.js', outcome: unchanged,
      source: "import { container, other as key } from './registry.js';\ncontainer.get(key).create();\n",
      files: { 'registry.js': "export const container = new Map(); export const key = Symbol('service'); export const other = Symbol('service');",
        'bootstrap.js': `${sdk}import { container, key } from './registry.js'; container.set(key, new Daytona());` } },
    { name: `non-client argument property defeats Daytona name (typed=${typed})`, typed, path: 'use.js', outcome: unchanged,
      source: 'export function handler(ctx) {\n  return ctx.daytona.create();\n}\n',
      files: { 'bootstrap.js': `${sdk}import { handler } from './use.js'; handler({ daytona: {} });` } },
    { name: `unrelated argument property (typed=${typed})`, typed: true, path: 'use.js', outcome: unchanged,
      source: 'export function handler(ctx) {\n  return ctx.client.create();\n}\n',
      files: { 'bootstrap.js': `${sdk}import { handler } from './use.js'; handler({ other: new Daytona(), client: opaque });` } },
    { name: `unrelated constructor factory invocation (typed=${typed})`, typed: true, path: 'use.js', outcome: unchanged,
      source: `${sdk}const make = Cls => new Cls();\nmake(Daytona);\nconst client = make(opaque);\nclient.create();\n` },
    { name: `namespace external export defeats Daytona module token (typed=${typed})`, typed, path: 'use.js', outcome: unchanged,
      source: "import * as infra from './daytona/index.js';\ninfra.client.get('/health');\n",
      files: { 'daytona/index.js': "export { default as client } from 'axios';" } },
    { name: `namespace re-export external defeats Daytona module token (typed=${typed})`, typed, path: 'use.js', outcome: unchanged,
      source: "import { services } from './barrel.js';\nservices.client.get('/health');\n",
      files: { 'barrel.js': "export * as services from './daytona/index.js';",
        'daytona/index.js': "export { default as client } from 'axios';" } },
    { name: `generic namespace opaque member (typed=${typed})`, typed, path: 'use.js', outcome: unchanged,
      source: "import * as infra from './provider.js';\ninfra.client.create();\n",
      files: { 'provider.js': `${sdk}export const client = globalThis.opaque;` } },
  ].filter(testCase => testCase.typed === typed)),
];
