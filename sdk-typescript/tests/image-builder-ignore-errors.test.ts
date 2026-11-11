/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { compilePattern } from '../src/v2/dockerignorePattern.js'
import { BadRequestError } from '../src/v2/errors/DaytonaError.js'

it.each(['a\\€', 'a\\\u00a0'])('refuses a non-ASCII regexp escape when compiling %s', (pattern) => {
  // Given a raw regexp escape Go refuses, including a trailing NBSP before trimming.
  // When the glob compiler evaluates it, then the SDK keeps the uniform refusal.
  expect(() => compilePattern(pattern)).toThrow(expect.objectContaining({
    constructor: BadRequestError,
    message: `invalid pattern ${JSON.stringify(pattern)}: invalid regular expression`,
  }))
})
