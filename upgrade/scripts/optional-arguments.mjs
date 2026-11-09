// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// A v2 form's optional v0 argument, `[[name@index|text]]` (both tools render
// it from the call: `text` with `$` as the argument's source when the call
// passed it, the `|literal` / `|zero` text for a numeric literal, nothing
// otherwise; typescript/src/optional-arguments.ts,
// python/src/daytona_upgrade/optional_arguments.py).
const MARKER = /\[\[(\w+)@(\d+)\|((?:(?!\]\]).)*)\]\]/g;

/** The form with every optional argument passed, spelled by its v0 name. */
export function withNamedArguments(form) {
  return form.replace(MARKER, (_marker, name, _index, body) => body.split('|')[0].split('$').join(name));
}

/**
 * The form with every optional argument passed as the numeric literal
 * `value` where the marker computes one (its `literal` or `zero` text), else
 * by its v0 name.
 */
export function withLiteralArguments(form, value) {
  return form.replace(MARKER, (_marker, name, _index, body) => {
    const [text, literal, zero = literal] = body.split('|');
    if (literal === undefined) return text.split('$').join(name);
    return (Number(value) === 0 ? zero : literal).split('$').join(value);
  });
}

export function hasLiteralArguments(form) {
  return [...form.matchAll(MARKER)].some((match) => match[3].includes('|'));
}

/** The form with every optional argument left out (v2's defaults). */
export function withoutOptionalArguments(form) {
  return form.replace(MARKER, '');
}

export function hasOptionalArguments(form) {
  return new RegExp(MARKER.source).test(form);
}
