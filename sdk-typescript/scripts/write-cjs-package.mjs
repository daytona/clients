/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// dist/cjs is a CommonJS package scope inside this "type": "module" package.
// It is also the nearest package.json for the CJS build, so it carries that
// build's own `#v0-loader` import (Node rejects `..` in an imports target).
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const cjs = join(root, 'dist', 'cjs')
mkdirSync(join(cjs, 'loaders'), { recursive: true })
for (const file of ['v0-node.cjs', 'v0-node-webpack.cjs', 'v0-browser.cjs']) {
  copyFileSync(join(root, 'loaders', 'cjs', file), join(cjs, 'loaders', file))
}
writeFileSync(
  join(cjs, 'package.json'),
  JSON.stringify({
    type: 'commonjs',
    imports: {
      '#v0-loader': {
        browser: './loaders/v0-browser.cjs',
        webpack: './loaders/v0-node-webpack.cjs',
        default: './loaders/v0-node.cjs',
      },
    },
  }),
)

// `@daytona/sdk/v2` does not export the v2 client class: SDK 2.x builds a
// client only through the root. A direct `new (require('@daytona/sdk/v2').Daytona)()`
// gets an error naming the root form. Stub functions, not throwing getters, so
// jest automocking can read them. The descriptor must start with `configurable`:
// cjs-module-lexer recognises descriptors starting with `value` or `enumerable`,
// so these stay invisible to ESM named imports.
{
  const v2Index = join(cjs, 'v2', 'index.js')
  const guards = ['Daytona', 'DaytonaV2'].map((name) => {
    const message =
      `\`${name}\` is not exported from '@daytona/sdk/v2': the v2 client is built only through the root client. ` +
      "Use `import { Daytona } from '@daytona/sdk'`, then `new Daytona().v2`."
    return `Object.defineProperty(exports, ${JSON.stringify(name)}, { configurable: true, writable: true, value: function ${name}() { throw new Error(${JSON.stringify(message)}) } })`
  })
  writeFileSync(
    v2Index,
    readFileSync(v2Index, 'utf8') +
      '\n// Added by scripts/write-cjs-package.mjs: SDK 2.x builds the v2 client only through the root.\n' +
      guards.join('\n') +
      '\n',
  )
}
