// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// A v2 form's optional v0 argument, `[[name@index|text]]` (both tools render
// it from the call: `text` with `$` as the argument's source when the call
// passed it, nothing otherwise; typescript/src/optional-arguments.ts,
// python/src/daytona_upgrade/optional_arguments.py).
const MARKER = /\[\[(\w+)@(\d+)\|((?:(?!\]\]).)*)\]\]/g;

/** The form with every optional argument passed, spelled by its v0 name. */
export function withNamedArguments(form) {
  return form.replace(MARKER, (_marker, name, _index, text) => text.split('$').join(name));
}

/** The form with every optional argument left out (v2's defaults). */
export function withoutOptionalArguments(form) {
  return form.replace(MARKER, '');
}

export function hasOptionalArguments(form) {
  return new RegExp(MARKER.source).test(form);
}
