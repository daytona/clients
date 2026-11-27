// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// ws loads these optional native add-ons inside try/catch; its documentation has bundlers
// leave them to Node, which is what a server app bundling the SDK would configure.
module.exports = {
  externalsType: 'commonjs',
  externals: { bufferutil: 'bufferutil', 'utf-8-validate': 'utf-8-validate' },
}
