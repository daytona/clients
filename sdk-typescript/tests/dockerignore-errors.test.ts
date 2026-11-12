/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Matcher } from '../src/v2/dockerignore.js'
import { BadRequestError } from '../src/v2/errors/DaytonaError.js'

it.each(['a\\€', 'a\\\u00a0x', '[[:foo:]]x', '[z-a]', '!a\\q'])('reports the raw regexp pattern with a stable error: %s', (pattern) => {
  // Given an invalid regexp, possibly an exception following an exclusion.
  const matcher = new Matcher(pattern.startsWith('!') ? ['*', pattern] : [pattern])
  // When evaluated, then the error is engine-independent and quotes the raw pattern.
  const action = (): boolean => matcher.excluded('a')
  expect(action).toThrow(expect.objectContaining({
    constructor: BadRequestError, message: `invalid pattern ${JSON.stringify(pattern)}: invalid regular expression`,
  }))
})

it('reports an illegal exclusion using the shared pattern error shape', () => {
  // Given an exclusion without a pattern.
  const pattern = '!'
  // When parsed, then the refusal uses the same quoted-pattern prefix.
  expect(() => new Matcher([pattern])).toThrow(expect.objectContaining({
    constructor: BadRequestError, message: 'invalid pattern "!": illegal exclusion pattern',
  }))
})
