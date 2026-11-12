**List and search pages are checked** — a search page needs its `total` and
`total_relation`; `total` must be a non-negative integer, the contract's
minimum of 0, so both SDKs reject a negative one (released versions read it as
given). A page's `next_cursor` may be omitted or `null`, and only a present
non-string one is refused (a mistyped cursor used to end `search()` /
`find_by_name()` early); `list()` checks `nextCursor` as it checks
`next_cursor`. Python no longer requires the per-item `cursor` of a list page
(an image or snapshot page item without one, or with a non-string one, was
refused); neither SDK reads it.
