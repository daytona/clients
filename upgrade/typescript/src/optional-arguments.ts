/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A v2 form may carry an optional v0 argument as `[[name@index|text]]`: the
 * `text` is rendered, with every `$` replaced by the argument's source, only
 * when the 0.x call passed that argument (positional `index`, or the keyword
 * `name` in Python); otherwise the marker is dropped and v2's default holds,
 * so the suggestion never names a variable the call did not have. A marker
 * may add `|literal` and `|zero` texts: for a numeric literal argument the
 * value is computed instead, `literal` with `$` as the literal (`zero` when it
 * is 0; `literal` again when no `zero` text is given), and a fourth `|none`
 * text is rendered for the language's null literal (`null` here, `None` in
 * Python). A required argument is marked `name@index!`: it is always
 * rendered, by its v0 name when the call's source for it is unknown. The
 * Python tool reads the same markers (optional_arguments.py). A
 * text that uses the argument more than once, or next to an arithmetic
 * operator, is a conversion expression: there an argument that is not a
 * name, a member chain or a literal is parenthesized, so its operators keep
 * their precedence.
 */
const MARKER = /\[\[(\w+)@(\d+)(!?)\|((?:(?!\]\]).)*)\]\]/g;
// A decimal numeric literal, written as both languages write one.
const NUMERIC_LITERAL = /^(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d+)?$/;
// A name or a member chain (`a`, `a.b.c`).
const SIMPLE = /^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$/;
// `$` as an operand of an arithmetic operator (`$ / 1000`).
const ARITHMETIC = /\$\s*[-+*/%]|[-+*/%]\s*\$/;
const NONE = 'null';
// A rendered `key: key` property, written in shorthand.
const SAME_NAME_PROPERTY = /^(, )?([A-Za-z_$][\w$]*): \2$/;

export function renderOptionalArguments(
  template: string,
  argument: (name: string, index: number) => string | undefined,
): string {
  return template.replace(MARKER, (_marker, name: string, index: string, required: string, body: string) => {
    const value = argument(name, Number(index)) ?? (required ? name : undefined);
    if (value === undefined) return '';
    const [text = '', literal, zero = literal, none] = body.split('|');
    const chosen = none !== undefined && value === NONE ? none
      : literal !== undefined && NUMERIC_LITERAL.test(value)
        ? (Number(value.replaceAll('_', '')) === 0 ? zero ?? literal : literal)
        : text;
    const parts = chosen.split('$');
    const conversion = parts.length > 2 || ARITHMETIC.test(chosen);
    const operand = conversion && !SIMPLE.test(value) && !NUMERIC_LITERAL.test(value) ? `(${value})` : value;
    return parts.join(operand).replace(SAME_NAME_PROPERTY, '$1$2');
  });
}

/** The form with every optional argument present, spelled by its v0 name (for the guide and checks). */
export function withNamedArguments(template: string): string {
  return renderOptionalArguments(template, (name) => name);
}
