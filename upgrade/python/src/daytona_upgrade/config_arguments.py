# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Preserve call delimiters after config keyword removal."""

from __future__ import annotations

import libcst as cst


def retain_argument_layout(call: cst.Call, args: list[cst.Arg]) -> cst.Call:
    """Keep retained commas and relocate the closing paren's indentation."""
    if len(args) == len(call.args):
        return call.with_changes(args=args)
    terminal = call.args[-1]
    if args and args[-1] is terminal:
        return call.with_changes(args=args)
    closing = (
        terminal.comma.whitespace_after
        if isinstance(terminal.comma, cst.Comma)
        else terminal.whitespace_after_arg
    )
    if not args:
        opening = call.whitespace_before_args
        if isinstance(opening, cst.ParenthesizedWhitespace):
            opening = opening.with_changes(
                empty_lines=(
                    (*opening.empty_lines, *closing.empty_lines)
                    if isinstance(closing, cst.ParenthesizedWhitespace)
                    else opening.empty_lines
                ),
                last_line=closing.last_line
                if isinstance(closing, cst.ParenthesizedWhitespace)
                else closing,
            )
        return call.with_changes(args=args, whitespace_before_args=opening)
    retained = args[-1]
    comma = retained.comma
    if isinstance(closing, cst.ParenthesizedWhitespace):
        if isinstance(comma, cst.Comma):
            spacing = comma.whitespace_after
            if isinstance(spacing, cst.ParenthesizedWhitespace):
                spacing = spacing.with_changes(
                    empty_lines=(*spacing.empty_lines, *closing.empty_lines),
                    last_line=closing.last_line,
                )
            else:
                spacing = closing.with_changes(
                    first_line=closing.first_line.with_changes(
                        whitespace=cst.SimpleWhitespace(""), comment=None
                    )
                )
            comma = comma.with_changes(whitespace_after=spacing)
    else:
        # Inline calls retain the original terminal comma convention.
        comma = terminal.comma
    return call.with_changes(args=[*args[:-1], retained.with_changes(comma=comma)])
