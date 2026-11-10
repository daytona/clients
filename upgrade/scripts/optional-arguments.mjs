// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// A v2 form's optional v0 argument, `[[name@index|text]]` (both tools render
// it from the call: `text` with `$` as the argument's source when the call
// passed it, the `|literal` / `|zero` text for a numeric literal, the `|none`
// text for the null literal, nothing otherwise (a required `name@index!`
// argument by its name); typescript/src/optional-arguments.ts,
// python/src/daytona_upgrade/optional_arguments.py).
const MARKER = /\[\[(\w+)@(\d+)(!?)\|((?:(?!\]\]).)*)\]\]/g;
// A rendered TypeScript `key: key` property, written in shorthand.
const SAME_NAME_PROPERTY = /^(, )?([A-Za-z_$][\w$]*): \2$/;

/** The form with every optional argument passed, spelled by its v0 name. */
export function withNamedArguments(form) {
  return form.replace(MARKER, (_marker, name, _index, _required, body) => body.split('|')[0].split('$').join(name).replace(SAME_NAME_PROPERTY, '$1$2'));
}

/**
 * The form with every optional argument passed as the numeric literal
 * `value` where the marker computes one (its `literal` or `zero` text), else
 * by its v0 name.
 */
export function withLiteralArguments(form, value) {
  return form.replace(MARKER, (_marker, name, _index, _required, body) => {
    const [text, literal, zero = literal] = body.split('|');
    if (literal === undefined) return text.split('$').join(name);
    return (Number(value) === 0 ? zero : literal).split('$').join(value);
  });
}

/**
 * The form with every optional argument passed as the language's null literal
 * (`None` / `null`) where the marker has a `none` text, else by its v0 name.
 */
export function withNoneArguments(form, nullLiteral) {
  return form.replace(MARKER, (_marker, name, _index, _required, body) => {
    const [text, , , none] = body.split('|');
    return none === undefined ? text.split('$').join(name) : none.split('$').join(nullLiteral);
  });
}

export function hasNoneArguments(form) {
  return [...form.matchAll(MARKER)].some((match) => match[4].split('|').length > 3);
}

export function hasLiteralArguments(form) {
  return [...form.matchAll(MARKER)].some((match) => match[4].includes('|'));
}

/** The form with every optional argument left out (v2's defaults). */
export function withoutOptionalArguments(form) {
  return form.replace(MARKER, (_marker, name, _index, required, body) =>
    required ? body.split('|')[0].split('$').join(name).replace(SAME_NAME_PROPERTY, '$1$2') : '');
}

export function hasOptionalArguments(form) {
  return new RegExp(MARKER.source).test(form);
}
