**Screenshot regions are checked** — `screen.screenshot(region=...)` requires
a `ComputerUseRegion` in Python and four integers in TypeScript, with `width`
and `height` above zero, before any request (`region.x must be an integer`,
`region.width must be greater than 0`). Python used to fail with
`AttributeError` on a mapping, and TypeScript sent a missing field as
`undefined` and a fraction as `"1.5"`. A coordinate beyond 2^53 − 1 either
way is refused in every SDK (`region.x must be between -9007199254740991 and
9007199254740991`). Go refuses a zero or negative `Width` / `Height` and the
same out-of-range values.
