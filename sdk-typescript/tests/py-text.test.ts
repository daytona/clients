/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { isBlank, strip } from '../src/v2/pyText'

// Python's str.isspace() set (tests/test_argument_checks.py pins the same characters).
it.each([
  ['\u001c\u0085', true],
  ['\ufeff', false],
  [' \t\n', true],
  ['a', false],
])('isBlank(%j) is %s, as Python', (value, blank) => {
  expect(isBlank(value)).toBe(blank)
})

it('strips the edges only, as Python', () => {
  expect(strip('\u0085 a b \u3000')).toBe('a b')
  expect(strip('\ufeffa')).toBe('\ufeffa')
})

it('strips a long internal whitespace run in linear time', () => {
  const name = `a${' '.repeat(200_000)}b${' '.repeat(200_000)}`
  const started = performance.now()
  expect(strip(name)).toBe(`a${' '.repeat(200_000)}b`)
  expect(isBlank(name)).toBe(false)
  expect(performance.now() - started).toBeLessThan(1_000)
})
