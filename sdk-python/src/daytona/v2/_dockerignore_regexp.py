# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Translate Go regexp classes and escapes to Python's regular expressions."""

import re

from ._dockerignore_class import _unicode_class, translate_class
from ._dockerignore_error import PatternError


def _go_regexp(expression: str) -> str:
    """Bridge Go's regexp escapes/classes, not Python's different extensions."""
    output, index = "", 0
    while index < len(expression):
        ch = expression[index]
        if ch == "[":
            translated, index = translate_class(expression, index)
            output += translated
            continue
        if ch == "\\" and index + 1 < len(expression):
            escaped = expression[index + 1]
            index += 2
            if escaped == "Q":
                end = expression.find(r"\E", index)
                output += re.escape(expression[index:] if end < 0 else expression[index:end])
                index = len(expression) if end < 0 else end + 2
                continue
            if escaped in "pP" and index < len(expression) and expression[index] in "CLMNPSZclmnpsz":
                output += "[" + _unicode_class(expression[index].upper(), escaped == "p") + "]"
                index += 1
                continue
            if escaped in "01234567":
                end = index
                while end < min(index + 2, len(expression)) and expression[end] in "01234567":
                    end += 1
                if escaped != "0" and end == index:
                    raise PatternError(expression, "invalid escape sequence")
                output += re.escape(chr(int(escaped + expression[index:end], 8)))
                index = end
                continue
            if escaped == "x":
                # The glob compiler escapes braces, so only two digits can reach here.
                digits = expression[index : index + 2]
                if len(digits) != 2 or any(c not in "0123456789abcdefABCDEF" for c in digits):
                    raise PatternError(expression, "invalid escape sequence")
                output += re.escape(chr(int(digits, 16)))
                index += 2
                continue
            if escaped in "sS":
                space = "\t\n\f\r " if escaped == "s" else "\x00-\x08\x0b\x0e-\x1f!-\\U0010ffff"
                output += "[" + space + "]"
                continue
            if ord(escaped) >= 128 or (escaped.isalnum() and escaped not in "abBAdDsSwWxzfnrtv"):
                raise PatternError(expression, "invalid escape sequence")
            output += r"\Z" if escaped == "z" else "\\" + escaped
            continue
        output += r"\Z" if ch == "$" else ch
        index += 1
    return output
