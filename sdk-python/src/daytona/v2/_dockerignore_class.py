# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Go regexp/syntax class items, emitted without host-regexp set syntax."""

import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from typing_extensions import assert_never

from ._dockerignore_error import PatternError

_POSIX: Final[dict[str, tuple[tuple[int, int], ...]]] = {
    "alnum": ((48, 57), (65, 90), (97, 122)),
    "alpha": ((65, 90), (97, 122)),
    "ascii": ((0, 127),),
    "blank": ((9, 9), (32, 32)),
    "cntrl": ((0, 31), (127, 127)),
    "digit": ((48, 57),),
    "graph": ((33, 126),),
    "lower": ((97, 122),),
    "print": ((32, 126),),
    "punct": ((33, 47), (58, 64), (91, 96), (123, 126)),
    "space": ((9, 13), (32, 32)),
    "upper": ((65, 90),),
    "word": ((48, 57), (65, 90), (95, 95), (97, 122)),
    "xdigit": ((48, 57), (65, 70), (97, 102)),
}
_PERL: Final = {"d": _POSIX["digit"], "s": ((9, 10), (12, 13), (32, 32)), "w": _POSIX["word"]}


@dataclass(frozen=True, slots=True)
class _Range:
    low: int
    high: int


@dataclass(frozen=True, slots=True)
class _AsciiSet:
    ranges: tuple[tuple[int, int], ...]
    complement: bool


@dataclass(frozen=True, slots=True)
class _UnicodeCategory:
    name: str
    positive: bool


@lru_cache(maxsize=14)
def _unicode_class(category: str, positive: bool) -> str:
    ranges: list[str] = []
    start = -1
    for value in range(0x110001):
        included = value < 0x110000 and unicodedata.category(chr(value)).startswith(category) == positive
        if included and start < 0:
            start = value
        if not included and start >= 0:
            ranges.append(f"\\U{start:08x}-\\U{value - 1:08x}")
            start = -1
    return "".join(ranges)


def _class_char(source: str, index: int) -> tuple[int, int]:
    """parseClassChar calls parseEscape, never a shorthand-set parser."""
    if index >= len(source):
        raise PatternError(source, "invalid regular expression")
    if source[index] != "\\":
        return ord(source[index]), index + 1
    index += 1
    if index >= len(source):
        raise PatternError(source, "invalid escape sequence")
    escaped = source[index]
    index += 1
    if escaped in "01234567":
        end = index
        while end < min(index + 2, len(source)) and source[end] in "01234567":
            end += 1
        if escaped != "0" and end == index:
            raise PatternError(source, "invalid escape sequence")
        return int(escaped + source[index:end], 8), end
    if escaped == "x":
        digits = source[index : index + 2]
        if len(digits) != 2 or any(c not in "0123456789abcdefABCDEF" for c in digits):
            raise PatternError(source, "invalid escape sequence")
        return int(digits, 16), index + 2
    controls = {"a": 7, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11}
    if escaped in controls:
        return controls[escaped], index
    if ord(escaped) < 128 and not escaped.isalnum():
        return ord(escaped), index
    raise PatternError(source, "invalid escape sequence")


def translate_class(source: str, start: int) -> tuple[str, int]:
    """Parse Go's Perl-mode item loop before emitting any host class contents."""
    index = start + 1
    negated = source.startswith("^", index)
    index += int(negated)
    items: list[_Range | _AsciiSet | _UnicodeCategory] = []
    # Once no `:]` follows, none follows any later item either: remembered, so
    # a long run of `[:` items is scanned once, not once per item.
    no_close = False
    while index < len(source) and (source[index] != "]" or not items):
        if not no_close and source.startswith("[:", index):
            end = source.find(":]", index + 2)
            no_close = end < 0
            if end >= 0:
                name = source[index + 2 : end]
                ranges = _POSIX.get(name.removeprefix("^"))
                if ranges is None:
                    raise PatternError(source, "invalid POSIX character class")
                items.append(_AsciiSet(ranges, name.startswith("^")))
                index = end + 2
                continue
        if source.startswith((r"\p", r"\P"), index):
            if index + 2 >= len(source) or source[index + 2] not in "CLMNPSZclmnpsz":
                raise PatternError(source, "invalid escape sequence")
            items.append(_UnicodeCategory(source[index + 2].upper(), source[index + 1] == "p"))
            index += 3
            continue
        if source[index] == "\\" and index + 1 < len(source) and source[index + 1] in "dDsSwW":
            escaped = source[index + 1]
            items.append(_AsciiSet(_PERL[escaped.lower()], escaped.isupper()))
            index += 2
            continue
        low, index = _class_char(source, index)
        high = low
        if source.startswith("-", index) and index + 1 < len(source) and source[index + 1] != "]":
            high, index = _class_char(source, index + 1)
            if high < low:
                raise PatternError(source, "invalid regular expression")
        items.append(_Range(low, high))
    if index >= len(source):
        raise PatternError(source, "invalid regular expression")
    emitted: list[str] = []
    for item in items:
        match item:
            case _Range(low, high):
                emitted.append(f"\\U{low:08x}-\\U{high:08x}")
            case _AsciiSet(ranges, complement):
                next_value = 0
                for low, high in ranges:
                    if complement:
                        if next_value < low:
                            emitted.append(f"\\U{next_value:08x}-\\U{low - 1:08x}")
                        next_value = high + 1
                    else:
                        emitted.append(f"\\U{low:08x}-\\U{high:08x}")
                if complement:
                    emitted.append(f"\\U{next_value:08x}-\\U0010ffff")
            case _UnicodeCategory(name, positive):
                emitted.append(_unicode_class(name, positive))
            case unreachable:
                assert_never(unreachable)
    return "[" + ("^" if negated else "") + "".join(emitted) + "]", index + 1
