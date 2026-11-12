// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { hasLiteralArguments, hasNoneArguments, hasOptionalArguments, withLiteralArguments, withNamedArguments, withNoneArguments, withoutOptionalArguments } from './optional-arguments.mjs';

// The forms the guide and the surface check render, for the markers both
// tools read (typescript/tests/optional-arguments.test.ts,
// python/tests/test_suggestions.py render the same forms from real calls).
const fork = '{sandbox}.fork({ [[params@0|...$, ]]memory: false[[timeout@1|, waitSeconds: $ === 0 ? 30 * 86_400 : $|, waitSeconds: $|, waitSeconds: 30 * 86_400]] })';
const start = '{sandbox}.start([[timeout@0|timeout=0 if $ is None else $|timeout=$]])';
const pause = '{sandbox}.pause([[timeout@0|{ timeoutSeconds: $ }]])';

for (const [form, named, without, literal, zero] of [
  [fork,
    '{sandbox}.fork({ ...params, memory: false, waitSeconds: timeout === 0 ? 30 * 86_400 : timeout })',
    '{sandbox}.fork({ memory: false })',
    '{sandbox}.fork({ ...params, memory: false, waitSeconds: 5 })',
    '{sandbox}.fork({ ...params, memory: false, waitSeconds: 30 * 86_400 })'],
  [start,
    '{sandbox}.start(timeout=0 if timeout is None else timeout)',
    '{sandbox}.start()',
    '{sandbox}.start(timeout=5)',
    '{sandbox}.start(timeout=0)'],
  [pause,
    '{sandbox}.pause({ timeoutSeconds: timeout })',
    '{sandbox}.pause()',
    '{sandbox}.pause({ timeoutSeconds: timeout })',
    '{sandbox}.pause({ timeoutSeconds: timeout })'],
]) {
  test(`renders ${form}`, () => {
    assert.equal(hasOptionalArguments(form), true);
    assert.equal(withNamedArguments(form), named);
    assert.equal(withoutOptionalArguments(form), without);
    assert.equal(withLiteralArguments(form, '5'), literal);
    assert.equal(withLiteralArguments(form, '0'), zero);
  });
}

test('renders the none text for the null literal', () => {
  const remove = '{sandbox}.delete([[timeout@0|timeout=60 if $ is None else $|timeout=$|timeout=$|timeout=60]])';
  assert.equal(hasNoneArguments(remove), true);
  assert.equal(hasNoneArguments(start), false);
  assert.equal(withNoneArguments(remove, 'None'), '{sandbox}.delete(timeout=60)');
  assert.equal(withNoneArguments(start, 'None'), '{sandbox}.start(timeout=0 if timeout is None else timeout)');
  assert.equal(withNoneArguments('f([[x@0|x=$|x=$|x=$|x=$ or 1]])', 'None'), 'f(x=None or 1)');
  assert.equal(withLiteralArguments(remove, '0'), '{sandbox}.delete(timeout=0)');
});

test('tells forms that convert a literal from those that do not', () => {
  assert.equal(hasLiteralArguments(fork), true);
  assert.equal(hasLiteralArguments(start), true);
  assert.equal(hasLiteralArguments(pause), false);
  assert.equal(hasOptionalArguments('{sandbox}.pause()'), false);
});

test('always renders a required argument, by its name when left out', () => {
  // The mapping's form.
  const type = '{sandbox}.computerUse.keyboard.type({ [[text@0!|text: $]][[delay@1|, delaySeconds: $ === undefined ? undefined : $ / 1000|, delaySeconds: $ / 1000|, delaySeconds: 0|]] })';
  assert.equal(withNamedArguments(type), '{sandbox}.computerUse.keyboard.type({ text, delaySeconds: delay === undefined ? undefined : delay / 1000 })');
  assert.equal(withoutOptionalArguments(type), '{sandbox}.computerUse.keyboard.type({ text })');
  assert.equal(withLiteralArguments(type, '50'), '{sandbox}.computerUse.keyboard.type({ text, delaySeconds: 50 / 1000 })');
  assert.equal(withLiteralArguments(type, '0'), '{sandbox}.computerUse.keyboard.type({ text, delaySeconds: 0 })');
  assert.equal(withNoneArguments(type, 'null'), '{sandbox}.computerUse.keyboard.type({ text })');
});
