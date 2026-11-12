/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Runs the bundled handler under lambda-local (the Lambda event / context
// contract, environment and timeout) and prints its response body.
const lambdaLocal = require('lambda-local')

lambdaLocal
  .execute({
    lambdaPath: require('node:path').resolve('dist/handler.js'),
    lambdaHandler: 'handler',
    event: {},
    environment: { DAYTONA_V2_API_URL: process.env.DAYTONA_V2_API_URL, DAYTONA_API_KEY: process.env.DAYTONA_API_KEY },
    timeoutMs: 30000,
    verboseLevel: 0,
  })
  .then((response) => process.stdout.write(response.body))
  .catch((error) => {
    console.error('Lambda invocation failed:', error)
    process.exit(1)
  })
